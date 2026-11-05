// Hearth lifecycle console. Plugins: analytics (KPIs from the metric views), genie (Hearth Campaigns, run as the
// signed-in user), lakebase (the plan, segments, lookups and approvals), serving (live lapse scores for new members).
import { createApp, analytics, genie, lakebase, server, serving } from '@databricks/appkit';
import { setupConsole } from './console/routes';

createApp({
  plugins: [analytics(), genie(), lakebase(), server(), serving()],
  async onPluginsReady(appkit) {
    await setupConsole(appkit);
  },
}).catch(console.error);
