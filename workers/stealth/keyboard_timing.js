// Keyboard timing: Gaussian delay mu=80ms, sigma=20ms, clamped [40, 150]ms.
// Exposes window.__stealth.generateKeyDelays(length) -> [delay_ms, ...].
(function () {
    window.__stealth = window.__stealth || {};

    function gaussRand(mu, sigma) {
        var u = 0, v = 0;
        while (u === 0) u = Math.random();
        while (v === 0) v = Math.random();
        return mu + sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
    }

    window.__stealth.generateKeyDelays = function (length) {
        var delays = [];
        for (var i = 0; i < length; i++) {
            var d = gaussRand(80, 20);
            delays.push(Math.max(40, Math.min(150, Math.round(d))));
        }
        return delays;
    };
})();
