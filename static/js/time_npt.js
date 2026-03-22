function formatUtcToNepali(utcTimestamp) {
  if (!utcTimestamp) return '-';

  const normalized = utcTimestamp.endsWith('Z')
    ? utcTimestamp
    : `${utcTimestamp.replace(' ', 'T')}Z`;

  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return utcTimestamp;

  try {
    const formatter = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Kathmandu',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });

    const parts = formatter.formatToParts(date).reduce((acc, part) => {
      acc[part.type] = part.value;
      return acc;
    }, {});

    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} NPT`;
  } catch (error) {
    const fallback = new Date(date.getTime() + 345 * 60 * 1000);
    const pad = value => String(value).padStart(2, '0');
    return `${fallback.getUTCFullYear()}-${pad(fallback.getUTCMonth() + 1)}-${pad(fallback.getUTCDate())} ${pad(fallback.getUTCHours())}:${pad(fallback.getUTCMinutes())}:${pad(fallback.getUTCSeconds())} NPT`;
  }
}
