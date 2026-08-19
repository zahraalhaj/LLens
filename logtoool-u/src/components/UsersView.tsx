import React, { useEffect, useState } from 'react';
import { UserPlus, Trash2, ShieldCheck, ShieldOff, KeyRound } from 'lucide-react';
import { api, ApiError } from '../api';
import { User } from '../types';

interface UsersViewProps {
  currentUserId: string;
}

export const UsersView: React.FC<UsersViewProps> = ({ currentUserId }) => {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<'admin' | 'member'>('member');
  const [creating, setCreating] = useState(false);

  const loadUsers = async () => {
    try {
      const data = await api.get<User[]>('/api/users');
      setUsers(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load users');
    }
  };

  useEffect(() => {
    loadUsers();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setCreating(true);
    try {
      await api.post('/api/users', { username, password, role });
      setUsername('');
      setPassword('');
      setRole('member');
      await loadUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to create user');
    } finally {
      setCreating(false);
    }
  };

  const handleToggleActive = async (u: User) => {
    try {
      await api.patch(`/api/users/${u.user_id}/active`, { is_active: !u.is_active });
      await loadUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to update user');
    }
  };

  const handleDelete = async (u: User) => {
    if (!window.confirm(`Permanently delete user '${u.username}'?`)) return;
    try {
      await api.delete(`/api/users/${u.user_id}`);
      await loadUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to delete user');
    }
  };

  const handleForcePasswordReset = async (u: User) => {
    if (!window.confirm(`Force '${u.username}' to set a new password on their next login?`)) return;
    try {
      await api.post(`/api/users/${u.user_id}/force-password-reset`);
      await loadUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to force password reset');
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-slate-900">Users</h2>
        <p className="text-sm text-slate-500">
          No self-service signup -- accounts are created here by an admin.
        </p>
      </div>

      {error && (
        <div className="text-sm font-medium text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-4 py-2.5">
          {error}
        </div>
      )}

      <form onSubmit={handleCreate} className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
        <h3 className="text-sm font-bold text-slate-800 mb-3 flex items-center gap-2">
          <UserPlus className="w-4 h-4 text-blue-600" /> Create user
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <input
            type="text"
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            className="px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <input
            type="password"
            placeholder="Password (min 8 chars)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            className="px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as 'admin' | 'member')}
            className="px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="member">Member</option>
            <option value="admin">Admin</option>
          </select>
          <button
            type="submit"
            disabled={creating}
            className="px-3 py-2 text-sm font-semibold bg-blue-600 hover:bg-blue-500 disabled:bg-blue-300 text-white rounded-md transition-all cursor-pointer"
          >
            {creating ? 'Creating…' : 'Create'}
          </button>
        </div>
      </form>

      <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">
            <tr>
              <th className="px-4 py-2.5">Username</th>
              <th className="px-4 py-2.5">Role</th>
              <th className="px-4 py-2.5">Status</th>
              <th className="px-4 py-2.5">Password</th>
              <th className="px-4 py-2.5">Created</th>
              <th className="px-4 py-2.5 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {users.map((u) => (
              <tr key={u.user_id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{u.username}</td>
                <td className="px-4 py-2.5 capitalize text-slate-600">{u.role}</td>
                <td className="px-4 py-2.5">
                  <span
                    className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                      u.is_active ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-slate-100 text-slate-500 border border-slate-200'
                    }`}
                  >
                    {u.is_active ? 'Active' : 'Deactivated'}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  {u.must_change_password ? (
                    <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
                      Change required
                    </span>
                  ) : (
                    <span className="text-xs text-slate-400">OK</span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-slate-500">{u.created_at?.slice(0, 10)}</td>
                <td className="px-4 py-2.5">
                  <div className="flex items-center justify-end gap-1.5">
                    <button
                      onClick={() => handleForcePasswordReset(u)}
                      disabled={!!u.must_change_password}
                      title="Force password reset on next login"
                      className="p-1.5 rounded-md text-amber-600 hover:bg-amber-50 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                    >
                      <KeyRound className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleToggleActive(u)}
                      disabled={u.user_id === currentUserId}
                      title={u.is_active ? 'Deactivate' : 'Reactivate'}
                      className="p-1.5 rounded-md text-slate-500 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                    >
                      {u.is_active ? <ShieldOff className="w-4 h-4" /> : <ShieldCheck className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => handleDelete(u)}
                      disabled={u.user_id === currentUserId}
                      title="Delete"
                      className="p-1.5 rounded-md text-rose-500 hover:bg-rose-50 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
