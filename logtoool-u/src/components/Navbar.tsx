import React from 'react';
import { LogOut } from 'lucide-react';
import { User } from '../types';

interface NavbarProps {
  pageTitle: string;
  pageDescription: string;
  user: User;
  onLogout: () => void;
}

export const Navbar: React.FC<NavbarProps> = ({ pageTitle, pageDescription, user, onLogout }) => {
  const initials = user.username.slice(0, 2).toUpperCase();

  return (
    <header className="bg-white border-b border-slate-200 px-6 py-3.5 flex items-center justify-between gap-4 shadow-2xs sticky top-0 z-20">
      <div className="min-w-0">
        <h1 className="text-lg font-extrabold text-slate-900 tracking-tight truncate">{pageTitle}</h1>
        <p className="text-xs text-slate-500 truncate">{pageDescription}</p>
      </div>

      <div className="flex items-center gap-2.5 shrink-0">
        <div className="w-8 h-8 rounded-full bg-blue-600 text-white text-xs font-bold flex items-center justify-center shrink-0">
          {initials}
        </div>
        <div className="hidden sm:block min-w-0">
          <div className="text-xs font-semibold text-slate-800 truncate max-w-[120px]">{user.username}</div>
          <div className="text-[10px] text-slate-400 capitalize">{user.role}</div>
        </div>
        <button
          onClick={onLogout}
          title="Sign out"
          className="p-1.5 rounded-md text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors cursor-pointer"
        >
          <LogOut className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
};
