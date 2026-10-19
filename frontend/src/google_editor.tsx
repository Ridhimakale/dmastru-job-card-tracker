import { useEffect, useState } from 'react';
import { api } from './api';
import { Button, Card } from './components';

type Kind = 'DOCUMENT' | 'SHEET';
type Props = { card: { id: number }; kind: Kind; title?:string; done?: () => void; targetWindow?: Window | null };
type GoogleStatus = { configured: boolean; connected: boolean; email?: string | null; configuration?: { client_id: boolean; client_secret: boolean; redirect_uri: boolean } };
type WorkProduct = { google_file_url: string };

/** Creates the selected Google file and takes the user straight to its editor. */
export function GoogleWorkProductEditor({ card, kind, title:requestedTitle, done, targetWindow }: Props) {
  const [message, setMessage] = useState('Preparing Google Docs…');
  const [error, setError] = useState('');
  const [needsConnect, setNeedsConnect] = useState(false);
  const [needsConfiguration, setNeedsConfiguration] = useState(false);
  const [configuration, setConfiguration] = useState<GoogleStatus['configuration']>();

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 60000);
    const timer = window.setTimeout(() => void (async () => {
      try {
        const status = await api<GoogleStatus>('/api/google-workspace/status', { signal: controller.signal });
        if (!active) return;
        if (!status.configured) {
          if (targetWindow && !targetWindow.closed) targetWindow.close();
          setNeedsConfiguration(true);
          setConfiguration(status.configuration);
          setMessage('Google OAuth settings are not visible to the backend yet. Start or restart the API from the PowerShell session that has the Google OAuth values set.');
          return;
        }
        if (!status.connected) {
          if (targetWindow && !targetWindow.closed) targetWindow.close();
          setNeedsConnect(true);
          setMessage('The Admin team must connect the shared Google Drive account from Settings before consultants can create Google documents.');
          return;
        }
        setMessage(`Opening Google ${kind === 'SHEET' ? 'Sheets' : 'Docs'}…`);
        const title = requestedTitle || `Job Card ${card.id} - ${kind === 'SHEET' ? 'Spreadsheet' : 'Document'}`;
        const file = await api<WorkProduct>(`/api/job-cards/${card.id}/google-work-products`, {
          method: 'POST',
          body: JSON.stringify({ kind, title }),
          signal: controller.signal,
        });
        if (active) {
          if (targetWindow && !targetWindow.closed) targetWindow.location.replace(file.google_file_url);
          else window.open(file.google_file_url, '_blank', 'noopener,noreferrer');
          setMessage(`Google ${kind === 'SHEET' ? 'Sheets' : 'Docs'} opened in a new tab.`);
          done?.();
        }
      } catch (e) {
        if (active && targetWindow && !targetWindow.closed) targetWindow.close();
        if (active) setError(e instanceof DOMException && e.name === 'AbortError'
          ? 'Google is taking too long to respond. Confirm the shared account is connected in Admin Settings and retry.'
          : e instanceof Error ? e.message : 'Unable to open Google Docs or Sheets.');
      } finally {
        window.clearTimeout(timeout);
      }
    })(), 0);
    return () => { active = false; window.clearTimeout(timer); window.clearTimeout(timeout); controller.abort(); };
  }, [card.id, kind, requestedTitle]);

  return <Card title={kind === 'SHEET' ? 'Google Sheets' : 'Google Docs'}>
    <p className="muted">{error || message}</p>
    {error && <Button variant="secondary" onClick={() => window.location.reload()}>Try again</Button>}
    {needsConfiguration && <>
      {configuration
        ? <p className="muted">API environment check: Client ID {configuration.client_id ? '✓' : 'missing'} · Client secret {configuration.client_secret ? '✓' : 'missing'} · Redirect URI {configuration.redirect_uri ? '✓' : 'missing'}.</p>
        : <p className="form-error" role="alert">The API on port 8001 is still running an older version. Restart that backend process to load the OAuth settings and diagnostic check.</p>}
      <p className="muted">The backend accepts GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI (or their GOOGLE_OAUTH_* equivalents).</p>
    </>}
    {needsConnect && <p className="muted">Ask an Admin to open Settings and connect the shared account.</p>}
  </Card>;
}
