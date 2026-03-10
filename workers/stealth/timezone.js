// Timezone matching — defaults to UTC, overridden via __stealth_timezone.
(function() {
    const targetTz = window.__stealth_timezone || 'UTC';

    const origDateTimeFormat = Intl.DateTimeFormat;
    const origResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;

    Intl.DateTimeFormat.prototype.resolvedOptions = function() {
        const opts = origResolvedOptions.call(this);
        opts.timeZone = targetTz;
        return opts;
    };
})();
