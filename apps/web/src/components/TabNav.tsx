export type TabKey =
  | "stats"
  | "databases"
  | "chats"
  | "messages"
  | "users"
  | "media"
  | "tombstones"
  | "events";

const TABS: { key: TabKey; label: string }[] = [
  { key: "messages", label: "Messages" },
  { key: "chats", label: "Chats" },
  { key: "media", label: "Media" },
  { key: "tombstones", label: "Tombstones" },
  { key: "events", label: "Log Events" },
  { key: "users", label: "Users" },
  { key: "databases", label: "Databases" },
  { key: "stats", label: "Overview" },
];

interface Props {
  active: TabKey;
  onChange: (k: TabKey) => void;
}

export default function TabNav({ active, onChange }: Props) {
  return (
    <nav className="flex bg-white border-b border-gray-200" role="tablist">
      {TABS.map((tab) => (
        <button
          key={tab.key}
          role="tab"
          aria-selected={active === tab.key}
          className={`flex-1 px-4 py-3 text-sm font-medium transition-colors border-b-2 ${
            active === tab.key
              ? "border-tg-primary text-tg-primary"
              : "border-transparent text-gray-600 hover:text-gray-900"
          }`}
          onClick={() => onChange(tab.key)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
