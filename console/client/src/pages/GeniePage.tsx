// Ask Genie: the Hearth Campaigns Genie Agent, embedded. Questions run as the signed-in marketer (on-behalf-of,
// through the app's dashboards.genie scope), so Genie sees exactly what that person may see.
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  GenieChatInput,
  GenieChatMessageList,
  useGenieChat,
} from '@databricks/appkit-ui/react';
import { useMemo } from 'react';
import { EmptyNote } from '../components/states';
import { useApi } from '../lib/api';

const STARTERS = [
  'Which nudges brought back the most members last week?',
  "What's the opt-out rate by nudge type?",
  'When did opt-outs start rising?',
  'How many members will we nudge tomorrow, and why are the rest held?',
];

export function GeniePage() {
  const me = useApi<{ email: string | null }>('/api/whoami');
  const { messages, status, error, sendMessage, reset } = useGenieChat({ alias: 'default' });
  const lastSql = useMemo(() => {
    for (const m of [...messages].reverse()) for (const a of m.attachments ?? []) if (a.query?.query) return a.query;
    return null;
  }, [messages]);
  const busy = status === 'streaming' || status === 'loading-history';

  return (
    <div className="space-y-4 w-full max-w-6xl mx-auto">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-2xl font-bold text-foreground">Ask Genie about campaigns</h2>
          <p className="text-sm text-muted-foreground mt-1">
            The Hearth Campaigns Genie Agent answers from the governed metric views: returns against the holdout, opt-outs and reach, lapse risk by cohort, and the plan for tomorrow.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">{me.data?.email ?? 'Signed in'}</Badge>
          <Button size="sm" variant="outline" onClick={reset} disabled={messages.length === 0}>New conversation</Button>
        </div>
      </div>

      <Alert>
        <AlertDescription>
          Questions run as you, with your own Unity Catalog permissions. Genie sees aggregates only: no member-level rows, names, contact details or sensitive attributes.
        </AlertDescription>
      </Alert>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 border rounded-lg flex flex-col h-[min(640px,70vh)]">
          {messages.length === 0 && !busy ? (
            <div className="flex-1 p-4 space-y-3 overflow-auto">
              <EmptyNote title="Ask a question about your campaigns">Try one of these:</EmptyNote>
              <div className="flex flex-wrap gap-2 justify-center">
                {STARTERS.map((q) => <Button key={q} size="sm" variant="outline" onClick={() => sendMessage(q)}>{q}</Button>)}
              </div>
            </div>
          ) : (
            <GenieChatMessageList messages={messages} status={status} className="flex-1" />
          )}
          {status === 'streaming' && <p className="px-4 text-sm text-muted-foreground">Genie is writing and running a query…</p>}
          {status === 'error' && (
            <Alert variant="destructive" className="mx-4 mb-2"><AlertDescription>{error ?? 'Genie could not answer. Rephrase or try again.'}</AlertDescription></Alert>
          )}
          <GenieChatInput onSend={sendMessage} disabled={busy} placeholder="Ask about returns, opt-outs, reach, lapse risk or tomorrow's plan" className="border-t p-2" />
          <p className="px-4 pb-2 text-xs text-muted-foreground">AI-generated from your data by Genie. Check the generated SQL before relying on a number.</p>
        </div>

        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>Generated SQL</CardTitle>
            <CardDescription>{lastSql?.title ?? lastSql?.description ?? 'How the latest answer was computed'}</CardDescription>
          </CardHeader>
          <CardContent>
            {lastSql?.query ? (
              <pre className="overflow-auto text-xs whitespace-pre-wrap bg-muted rounded-md p-3 max-h-[480px]">{lastSql.query}</pre>
            ) : (
              <p className="text-sm text-muted-foreground">The query Genie wrote appears here after it answers.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
