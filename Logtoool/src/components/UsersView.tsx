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
        <h2 className="text-xl font-bold text-text">Users</h2>
        <p className="text-sm text-text-muted">
          No self-service signup -- accounts are created here by an admin.
        </p>
      </div>

      {error && (
        <div className="text-sm font-medium text-error bg-error-light border border-error/20 rounded-lg px-4 py-2.5">
          {error}
        </div>
      )}

      <form onSubmit={handleCreate} className="bg-surface border-surface-border border rounded-2xl card-brand-glow p-5 shadow-sm">
        <h3 className="text-sm font-bold text-text mb-3 flex items-center gap-2">
          <UserPlus className="w-4 h-4 text-brand" /> Create user
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <input
            type="text"
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            className="px-3 py-2 text-sm bg-surface-alt border-surface-border border rounded-lg focus:outline-none focus:ring-2 input-brand"
          />
          <input
            type="password"
            placeholder="Password (min 8 chars)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            className="px-3 py-2 text-sm bg-surface-alt border-surface-border border rounded-lg focus:outline-none focus:ring-2 input-brand"
          />
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as 'admin' | 'member')}
            className="px-3 py-2 text-sm bg-surface-alt border-surface-border border rounded-lg focus:outline-none focus:ring-2 input-brand"
          >
            <option value="member">Member</option>
            <option value="admin">Admin</option>
          </select>
          <button
            type="submit"
            disabled={creating}
            className="px-3 py-2 text-sm font-semibold bg-brand hover:bg-brand-hover disabled:bg-brand/50 text-white rounded-lg shadow-md shadow-brand/20 transition-all cursor-pointer"
          >
            {creating ? 'Creating…' : 'Create'}
          </button>
        </div>
      </form>

      <div className="bg-surface border-surface-border border rounded-2xl card-brand-glow shadow-sm overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-surface-alt border-b border-surface-border text-left text-xs font-semibold text-text-muted uppercase tracking-wide">
            <tr>
              <th className="px-4 py-2.5">Username</th>
              <th className="px-4 py-2.5">Role</th>
              <th className="px-4 py-2.5">Status</th>
              <th className="px-4 py-2.5">Password</th>
              <th className="px-4 py-2.5">Created</th>
              <th className="px-4 py-2.5 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-border">
            {users.map((u) => (
              <tr key={u.user_id} className="table-row-brand">
                <td className="px-4 py-2.5 font-medium text-text">{u.username}</td>
                <td className="px-4 py-2.5 capitalize text-text-secondary">{u.role}</td>
                <td className="px-4 py-2.5">
                  <span
                    className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                      u.is_active ? 'bg-success-light text-success border border-success/20' : 'bg-surface-alt text-text-muted border border-surface-border'
                    }`}
                  >
                    {u.is_active ? 'Active' : 'Deactivated'}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  {u.must_change_password ? (
                    <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-warning-light text-warning border border-warning/20">
                      Change required
                    </span>
                  ) : (
                    <span className="text-xs text-text-muted">OK</span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-text-muted">{u.created_at?.slice(0, 10)}</td>
                <td className="px-4 py-2.5">
                  <div className="flex items-center justify-end gap-1.5">
                    <button
                      onClick={() => handleForcePasswordReset(u)}
                      disabled={!!u.must_change_password}
                      title="Force password reset on next login"
                      className="p-1.5 rounded-md text-warning hover:bg-warning-light disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                    >
                      <KeyRound className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleToggleActive(u)}
                      disabled={u.user_id === currentUserId}
                      title={u.is_active ? 'Deactivate' : 'Reactivate'}
                      className="p-1.5 rounded-md text-text-muted hover:bg-surface-alt disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                    >
                      {u.is_active ? <ShieldOff className="w-4 h-4" /> : <ShieldCheck className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => handleDelete(u)}
                      disabled={u.user_id === currentUserId}
                      title="Delete"
                      className="p-1.5 rounded-md text-error hover:bg-error-light disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
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
