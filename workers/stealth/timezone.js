// Timezone override — defaults to UTC, configurable via __stealth_timezone.
(function () {
    var tz = window.__stealth_timezone || 'UTC';

    var origResolved = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function () {
        var opts = origResolved.call(this);
        opts.timeZone = tz;
        return opts;
    };
})();
