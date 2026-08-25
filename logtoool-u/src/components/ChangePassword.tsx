import React, { useState } from 'react';
import { Cpu, KeyRound, Eye, EyeOff } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';

export const ChangePassword: React.FC = () => {
  const { user, changePassword, error, logout } = useAuth();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPasswords, setShowPasswords] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const fieldType = showPasswords ? 'text' : 'password';

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
    <div className="min-h-screen bg-slate-100 flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-3 justify-center mb-8">
          <div className="bg-amber-500 text-white p-2 rounded-lg font-bold flex items-center justify-center w-10 h-10 shadow-xs">
            <KeyRound className="w-6 h-6" />
          </div>
          <div>
            <h1 className="text-xl font-extrabold text-slate-900 tracking-tight">Set a new password</h1>
            <p className="text-xs text-slate-500">Required before you can continue</p>
          </div>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-6 space-y-4">
          <p className="text-xs text-slate-600 leading-relaxed">
            Signed in as <strong className="text-slate-800">{user?.username}</strong>. Your account's
            current password was set by an administrator and must be changed before you can use the app.
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="current" className="block text-xs font-semibold text-slate-700 mb-1.5">
                Current password
              </label>
              <input
                id="current"
                type={fieldType}
                autoComplete="current-password"
                autoFocus
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                required
              />
            </div>

            <div>
              <label htmlFor="newpw" className="block text-xs font-semibold text-slate-700 mb-1.5">
                New password
              </label>
              <input
                id="newpw"
                type={fieldType}
                autoComplete="new-password"
                minLength={8}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                required
              />
            </div>

            <div>
              <label htmlFor="confirmpw" className="block text-xs font-semibold text-slate-700 mb-1.5">
                Confirm new password
              </label>
              <input
                id="confirmpw"
                type={fieldType}
                autoComplete="new-password"
                minLength={8}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                required
              />
            </div>

            <button
              type="button"
              onClick={() => setShowPasswords((v) => !v)}
              className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 cursor-pointer -mt-1"
            >
              {showPasswords ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              {showPasswords ? 'Hide passwords' : 'Show passwords'}
            </button>

            {(localError || error) && (
              <div className="text-xs font-medium text-rose-700 bg-rose-50 border border-rose-200 rounded-md px-3 py-2">
                {localError || error}
              </div>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 text-sm font-semibold bg-blue-600 hover:bg-blue-500 disabled:bg-blue-300 text-white rounded-md transition-all cursor-pointer disabled:cursor-not-allowed"
            >
              {submitting ? 'Setting password…' : 'Set new password & continue'}
            </button>

            <button
              type="button"
              onClick={() => logout()}
              className="w-full text-xs text-slate-400 hover:text-slate-600 text-center cursor-pointer"
            >
              Sign out instead
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};
