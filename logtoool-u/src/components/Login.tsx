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
    <div className="min-h-screen bg-slate-100 flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-3 justify-center mb-8">
          <div className="bg-blue-600 text-white p-2 rounded-lg font-bold flex items-center justify-center w-10 h-10 shadow-xs">
            <Cpu className="w-6 h-6" />
          </div>
          <div>
            <h1 className="text-xl font-extrabold text-slate-900 tracking-tight">LOGTOOL</h1>
            <p className="text-xs text-slate-500">Log Visualization & Analytics</p>
          </div>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-white border border-slate-200 rounded-xl shadow-sm p-6 space-y-4"
        >
          <div>
            <label htmlFor="username" className="block text-xs font-semibold text-slate-700 mb-1.5">
              Username
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              required
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-xs font-semibold text-slate-700 mb-1.5">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              required
            />
          </div>

          {error && (
            <div className="text-xs font-medium text-rose-700 bg-rose-50 border border-rose-200 rounded-md px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 text-sm font-semibold bg-blue-600 hover:bg-blue-500 disabled:bg-blue-300 text-white rounded-md transition-all cursor-pointer disabled:cursor-not-allowed"
          >
            <LogIn className="w-4 h-4" />
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>

          <p className="text-xs text-slate-400 text-center pt-1">
            No self-service signup -- ask an admin to create your account.
          </p>
        </form>
      </div>
    </div>
  );
};
