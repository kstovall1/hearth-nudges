import { createBrowserRouter, NavLink, Outlet, RouterProvider } from 'react-router';
import { useState } from 'react';
import { Badge, Button, Sheet, SheetContent, SheetHeader, SheetTitle, useIsMobile } from '@databricks/appkit-ui/react';
import { Menu } from 'lucide-react';
import { useApi } from './lib/api';
import { int, when } from './lib/format';
import { OverviewPage } from './pages/OverviewPage';
import { PlanPage } from './pages/PlanPage';
import { CopyPage } from './pages/CopyPage';
import { MemberPage } from './pages/MemberPage';
import { GeniePage } from './pages/GeniePage';

const PAGES = [
  { to: '/', label: 'Overview', end: true },
  { to: '/plan', label: "Tomorrow's plan" },
  { to: '/copy', label: 'Copy review' },
  { to: '/members', label: 'Member lookup' },
  { to: '/genie', label: 'Ask Genie' },
];

const linkClass = (block: boolean) => ({ isActive }: { isActive: boolean }) =>
  `${block ? 'block py-2' : 'py-1.5'} px-3 rounded-md text-sm font-medium transition-colors ${
    isActive ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
  }`;

function Nav({ block, onClick }: { block: boolean; onClick?: () => void }) {
  return (
    <nav className={block ? 'flex flex-col gap-1' : 'hidden md:flex gap-1'}>
      {PAGES.map((p) => (
        <NavLink key={p.to} to={p.to} end={p.end} className={linkClass(block)} onClick={onClick}>{p.label}</NavLink>
      ))}
    </nav>
  );
}

interface Status { decision_date: string | null; decided_at: string | null; copy_checked_at: string | null; sends: number; members: number; ms: number }

function Layout() {
  const isMobile = useIsMobile();
  const [open, setOpen] = useState(false);
  const status = useApi<Status>('/api/status');
  return (
    <div className="min-h-screen bg-background flex flex-col">
      <header className="border-b px-4 md:px-6 py-3 flex items-center gap-4">
        <h1 className="text-lg font-semibold text-foreground whitespace-nowrap">Hearth lifecycle console</h1>
        <Nav block={false} />
        <div className="ml-auto hidden lg:flex items-center gap-2">
          {status.data && (
            <Badge variant="outline" title={`Lakebase answered in ${status.data.ms} ms`}>
              Plan for {status.data.decision_date}: {int(status.data.sends)} sends, copy checked {when(status.data.copy_checked_at)}
            </Badge>
          )}
          {status.error && <Badge variant="destructive">Lakebase unavailable</Badge>}
        </div>
        <div className="ml-auto md:hidden">
          <Sheet open={open && isMobile} onOpenChange={setOpen}>
            <Button variant="ghost" size="icon" onClick={() => setOpen(true)}>
              <Menu className="h-5 w-5" />
              <span className="sr-only">Open navigation</span>
            </Button>
            <SheetContent side="left">
              <SheetHeader><SheetTitle>Hearth</SheetTitle></SheetHeader>
              <Nav block onClick={() => setOpen(false)} />
            </SheetContent>
          </Sheet>
        </div>
      </header>
      <main className="flex-1 p-4 md:p-6">
        <Outlet />
      </main>
      <footer className="border-t px-4 md:px-6 py-2 text-xs text-muted-foreground">
        Synthetic data for Hearth, a fictional dating app. KPIs come from Unity Catalog metric views, the plan and approvals from Lakebase.
      </footer>
    </div>
  );
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: <OverviewPage /> },
      { path: '/plan', element: <PlanPage /> },
      { path: '/copy', element: <CopyPage /> },
      { path: '/members', element: <MemberPage /> },
      { path: '/genie', element: <GeniePage /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
