import React, { useState } from 'react';
import { Cpu, KeyRound } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';

export const ChangePassword: React.FC = () => {
  const { user, changePassword, error, logout } = useAuth();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLocalError(null);

    if (newPassword.length < 8) {
      setLocalError('New password must be at least 8 characters.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setLocalError('New password and confirmation do not match.');
      return;
    }
    if (newPassword === currentPassword) {
      setLocalError('New password must be different from your current password.');
      return;
    }

    setSubmitting(true);
    try {
      await changePassword(currentPassword, newPassword);
    } catch {
      // error surfaced via useAuth().error
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-surface-alt flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        {/* Brand */}
        <div className="flex items-center gap-3 justify-center mb-8">
          <div className="w-12 h-12 bg-warning rounded-xl flex items-center justify-center shadow-lg shadow-warning/30">
            <KeyRound className="w-7 h-7 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-extrabold text-text tracking-tight">Set a new password</h1>
            <p className="text-xs text-text-secondary">Required before you can continue</p>
          </div>
        </div>

        {/* Form */}
        <div className="bg-surface border border-surface-border rounded-2xl shadow-lg p-6 space-y-4">
          <p className="text-xs text-text-secondary leading-relaxed">
            Signed in as <strong className="text-text">{user?.username}</strong>. Your account's
            current password was set by an administrator and must be changed before you can use the app.
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="current" className="block text-xs font-semibold text-text mb-1.5">
                Current password
              </label>
              <input
                id="current"
                type="password"
                autoComplete="current-password"
                autoFocus
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="w-full px-3 py-2.5 text-sm border border-surface-border rounded-lg bg-surface-alt text-text focus:outline-none input-brand"
                required
              />
            </div>

            <div>
              <label htmlFor="newpw" className="block text-xs font-semibold text-text mb-1.5">
                New password
              </label>
              <input
                id="newpw"
                type="password"
                autoComplete="new-password"
                minLength={8}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full px-3 py-2.5 text-sm border border-surface-border rounded-lg bg-surface-alt text-text focus:outline-none input-brand"
                required
              />
            </div>

            <div>
              <label htmlFor="confirmpw" className="block text-xs font-semibold text-text mb-1.5">
                Confirm new password
              </label>
              <input
                id="confirmpw"
                type="password"
                autoComplete="new-password"
                minLength={8}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full px-3 py-2.5 text-sm border border-surface-border rounded-lg bg-surface-alt text-text focus:outline-none input-brand"
                required
              />
            </div>

            {(localError || error) && (
              <div className="text-xs font-medium text-error bg-error-light border border-error/20 rounded-lg px-3 py-2">
                {localError || error}
              </div>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="w-full flex items-center justify-center gap-2 px-3 py-2.5 text-sm font-semibold bg-brand hover:bg-brand-hover disabled:opacity-50 text-white rounded-lg transition-all cursor-pointer disabled:cursor-not-allowed shadow-md shadow-brand/20"
            >
              {submitting ? 'Setting password…' : 'Set new password & continue'}
            </button>

            <button
              type="button"
              onClick={() => logout()}
              className="w-full text-xs text-text-muted hover:text-text text-center cursor-pointer"
            >
              Sign out instead
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};
