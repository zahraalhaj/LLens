import React, { useState } from 'react';
import { Cpu, LogIn } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';

export const Login: React.FC = () => {
  const { login, error } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await login(username, password);
    } catch {
      // error is already surfaced via useAuth().error
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-surface-alt flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        {/* Brand */}
        <div className="flex items-center gap-3 justify-center mb-8">
          <div className="w-12 h-12 bg-brand-gradient rounded-xl flex items-center justify-center shadow-xl shadow-brand/30">
            <Cpu className="w-7 h-7 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold text-text tracking-tight">LLENS</h1>
            <p className="text-xs text-text-secondary">Log Visualization & Analytics</p>
          </div>
        </div>

        {/* Form */}
        <form
          onSubmit={handleSubmit}
          className="bg-surface border border-surface-border rounded-2xl shadow-lg p-6 space-y-4"
        >
          <div>
            <label htmlFor="username" className="block text-xs font-semibold text-text mb-1.5">
              Username
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2.5 text-sm border border-surface-border rounded-lg bg-surface-alt text-text placeholder:text-text-muted focus:outline-none input-brand"
              required
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-xs font-semibold text-text mb-1.5">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2.5 text-sm border border-surface-border rounded-lg bg-surface-alt text-text placeholder:text-text-muted focus:outline-none input-brand"
              required
            />
          </div>

          {error && (
            <div className="text-xs font-medium text-error bg-error-light border border-error/20 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full flex items-center justify-center gap-2 px-3 py-2.5 text-sm font-semibold bg-brand hover:bg-brand-hover disabled:opacity-50 text-white rounded-lg transition-all cursor-pointer disabled:cursor-not-allowed shadow-md shadow-brand/20"
          >
            <LogIn className="w-4 h-4" />
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>

          <p className="text-xs text-text-muted text-center pt-1">
            No self-service signup — ask an admin to create your account.
          </p>
        </form>
      </div>
    </div>
  );
};
