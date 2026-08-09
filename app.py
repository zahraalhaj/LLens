import streamlit as st

from core import db, matcher, parser, profiles, reader

st.set_page_config(page_title="LLens — Log Analysis", layout="wide")

st.title("LLens — Log Analysis")
st.caption("Upload a log file. The system detects its format, parses it into a canonical "
           "event model, stores it, and visualizes it.")

with st.sidebar:
    st.header("Settings")
    db_path = st.text_input("Database path", "data/llens.db")
    profile_names = ["Auto-detect"] + profiles.list_profiles()
    profile_choice = st.selectbox("Parser profile", profile_names, index=0)

    with st.expander("Uploaded batches"):
        con = db.get_connection(db_path)
        batches = db.batches_to_df(con)
        if batches.empty:
            st.info("No batches yet.")
        else:
            st.dataframe(batches, use_container_width=True)

uploaded = st.file_uploader("Upload a log file", type=["log", "txt", "out"])

if uploaded is None:
    st.info("Upload a log file to begin.")
    st.stop()

text = uploaded.getvalue().decode("utf-8", errors="replace")
entries = reader.read_entries(text)

if not entries:
    st.warning("The file appears to be empty.")
    st.stop()

all_profiles = list(profiles.load_all().values())
if profile_choice == "Auto-detect":
    profile, ratio = matcher.select(entries, all_profiles)
else:
    profile = next(p for p in all_profiles if p["name"] == profile_choice)
    ratio = matcher.score(entries, profile)

confident = ratio >= profile.get("min_confidence", 0.6)

st.subheader("Pipeline")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Detected format", profile["name"])
c2.metric("Match ratio", f"{ratio:.0%}")
c3.metric("Log entries", len(entries))
c4.metric("Confidence", "OK" if confident else "LOW")

if not confident:
    st.warning(f"No profile matches confidently (best: **{profile['name']}** at {ratio:.0%}). "
               "AI-assisted onboarding for new formats arrives in a later milestone.")

if st.button("Parse and store", type="primary"):
    with st.spinner("Parsing and storing events..."):
        batch_id = db.insert_batch(
            con,
            file_name=uploaded.name,
            source_system=profile.get("source_system"),
            profile_name=profile["name"],
            structure=profile.get("structure"),
            match_ratio=ratio,
            row_count=len(entries),
        )
        events = parser.parse_entries(entries, profile, batch_id)
        db.insert_events(con, events)

    st.success(f"Stored {len(events)} events (batch #{batch_id})")

    df = db.events_to_df(con, batch_id)

    st.subheader("Summary")
    m1, m2, m3 = st.columns(3)
    m1.metric("Events parsed", len(df))
    m2.metric("Events with errors+", int(df["level"].isin(["ERROR", "CRITICAL"]).sum()))
    m3.metric("Unparsed lines", len(entries) - len(df))

    st.subheader("Events")
    st.dataframe(
        df.drop(columns=["raw", "attributes"]),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Raw log"):
        st.code("\n".join(e["raw"] for e in entries), language=None)
else:
    st.info("Click **Parse and store** to ingest this file into the database.")
