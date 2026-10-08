import { formatElapsedTime } from '../../../../lib/elapsedTime';
export const duration = (ms: number) => formatElapsedTime(ms / 1000);
export const timestamp = (value?: string) => value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'long' }).format(new Date(value)) : 'Not reported';
export const deadline = (value?: string) => !value ? 'Not reported' : Date.parse(value) <= Date.now() ? 'Deadline reached; awaiting backend state' : `${duration(Date.parse(value) - Date.now())} remaining (display only)`;
export const name = (value?: string) => value ? value.replaceAll('_', ' ') : 'Not reported';
export const dependencyName = (value?: string) => value === 'power_automate_gateway' ? 'Power Automate gateway' : name(value);
export const errorExplanation = (code?: string) => !code ? 'None reported' : code === 'Unknown' ? 'Unknown error' : code.endsWith('TIMEOUT') ? 'The configured deadline was reached.' : code === 'TURN_STALLED' ? 'The backend reported stalled progress.' : code === 'TURN_CANCELLED' ? 'The backend reported cancellation.' : 'The backend reported this canonical error category.';
