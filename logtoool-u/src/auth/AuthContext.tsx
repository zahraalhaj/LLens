import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api, ApiError, setUnauthorizedHandler } from '../api';
import { User } from '../types';
import { CHAT_HISTORY_STORAGE_KEY } from '../components/ChatView';

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
  }, []);

  useEffect(() => {
    api
      .get<User>('/api/auth/me')
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setError(null);
    try {
      const loggedInUser = await api.post<User>('/api/auth/login', { username, password });
      setUser(loggedInUser);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError('Login failed. Please try again.');
      }
      throw err;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post('/api/auth/logout');
    } finally {
      setUser(null);
      sessionStorage.removeItem(CHAT_HISTORY_STORAGE_KEY);
    }
  }, []);

  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    setError(null);
    try {
      const updatedUser = await api.post<User>('/api/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setUser(updatedUser);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError('Failed to change password. Please try again.');
      }
      throw err;
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, error, login, logout, changePassword }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
