import { useState } from "react";
import TabNav, { type TabKey } from "./components/TabNav";
import StatsTab from "./components/StatsTab";
import DatabasesTab from "./components/DatabasesTab";
import ChatsTab from "./components/ChatsTab";
import MessagesTab from "./components/MessagesTab";
import UsersTab from "./components/UsersTab";
import MediaTab from "./components/MediaTab";
import TombstonesTab from "./components/TombstonesTab";
import LogEventsTab from "./components/LogEventsTab";

export default function App() {
  const [active, setActive] = useState<TabKey>("messages");
  // Pre-fill the Chats tab search when the Users tab cross-links to it.
  const [chatsInitialSearch, setChatsInitialSearch] = useState("");

  const handleUserClick = (name: string) => {
    setChatsInitialSearch(name);
    setActive("chats");
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow-sm">
        <div className="max-w-6xl mx-auto px-6 py-5">
          <h1 className="text-2xl font-bold text-gray-900">Telegram Data Viewer</h1>
        </div>
      </header>

      <div className="max-w-6xl mx-auto mt-6 bg-white rounded-lg shadow-sm overflow-hidden">
        <TabNav active={active} onChange={(k) => { setActive(k); if (k !== "chats") setChatsInitialSearch(""); }} />
        <div className="p-6">
          {active === "stats" && <StatsTab />}
          {active === "databases" && <DatabasesTab />}
          {active === "chats" && <ChatsTab initialSearch={chatsInitialSearch} />}
          {active === "messages" && <MessagesTab />}
          {active === "users" && <UsersTab onUserClick={handleUserClick} />}
          {active === "media" && <MediaTab />}
          {active === "tombstones" && <TombstonesTab />}
          {active === "events" && <LogEventsTab />}
        </div>
      </div>
    </div>
  );
}
