import {
  Upload,
  Search,
  BellRing,
  MessageSquareCode,
  Settings,
  Terminal,
  ShieldQuestion,
  Users as UsersIcon,
  ServerCog,
  LayoutDashboard,
  BrainCircuit,
  type LucideIcon,
} from 'lucide-react';

export type TabType =
  | 'upload'
  | 'explore'
  | 'stats'
  | 'payment-monitoring'
  | 'analytics'
  | 'alerts'
  | 'chat'
  | 'ai-analyst'
  | 'settings'
  | 'users'
  | 'control-center';

export interface NavItem {
  id: TabType;
  label: string;
  description: string;
  icon: LucideIcon;
  adminOnly?: boolean;
}

export interface NavSection {
  title: string;
  items: NavItem[];
}

// Single source of truth for the app's information architecture --
// Sidebar renders it grouped, App/Navbar read it flat for the page
// title + breadcrumb. Keep labels/descriptions here, not in components.
export const NAV_SECTIONS: NavSection[] = [
  {
    title: 'Ingestion',
    items: [
      { id: 'upload', label: 'Upload & Ingest', description: 'Import and parse new log files', icon: Upload },
      { id: 'explore', label: 'Explore Events', description: 'Search and inspect individual log events', icon: Search },
    ],
  },
  {
    title: 'Analytics',
    items: [
      { id: 'stats', label: 'Analytics & Stats', description: 'Ingestion volume, sources, error rates, and event timeline', icon: Terminal },
      { id: 'alerts', label: 'Alerts Center', description: 'Alert rules and dispatch history', icon: BellRing },
    ],
  },
  {
    title: 'Payment Rail Monitoring',
    items: [
      {
        id: 'payment-monitoring',
        label: 'Payment Rail Monitoring',
        description: 'V+, OTP Processor, Debit Portal, Cardinal, and VFlex flows',
        icon: ShieldQuestion,
      },
      {
        id: 'analytics',
        label: 'Cross-Log Analytics',
        description: 'Service overview, dependency health, queues, investigation, and data quality',
        icon: LayoutDashboard,
      },
    ],
  },
  {
    title: 'Assistant',
    items: [
      { id: 'chat', label: 'Chat With Logs', description: 'Ask questions about ingested log data', icon: MessageSquareCode },
      {
        id: 'ai-analyst',
        label: 'AI Analyst',
        description: 'Ask questions about analyzed transactions, answered only from the deterministic analytical model',
        icon: BrainCircuit,
      },
    ],
  },
  {
    title: 'Administration',
    items: [
      { id: 'settings', label: 'Profiles & Settings', description: 'Parser profiles and application settings', icon: Settings },
      { id: 'users', label: 'Users', description: 'Manage user accounts and roles', icon: UsersIcon, adminOnly: true },
      { id: 'control-center', label: 'Control Center', description: 'Remote machine polling and system control', icon: ServerCog, adminOnly: true },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);

export function getNavItem(id: TabType): NavItem | undefined {
  return NAV_ITEMS.find((item) => item.id === id);
}
