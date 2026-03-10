// Mouse movement: Bezier curves with 10-20 steps, 20-50ms intervals,
// Gaussian jitter sigma=2px.
// Exposes window.__stealth.generateMousePath(x0, y0, x1, y1) -> [{x, y, delay}].
(function () {
    window.__stealth = window.__stealth || {};

    function gaussRand(mu, sigma) {
        var u = 0, v = 0;
        while (u === 0) u = Math.random();
        while (v === 0) v = Math.random();
        return mu + sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
    }

    function bezier(t, p0, p1, p2, p3) {
        var u = 1 - t;
        return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3;
    }

    window.__stealth.generateMousePath = function (x0, y0, x1, y1) {
        var steps = Math.floor(Math.random() * 11) + 10; // 10-20
        var dx = x1 - x0, dy = y1 - y0;
        var cp1x = x0 + dx * 0.25 + (Math.random() - 0.5) * Math.abs(dx) * 0.3;
        var cp1y = y0 + dy * 0.25 + (Math.random() - 0.5) * Math.abs(dy) * 0.3;
        var cp2x = x0 + dx * 0.75 + (Math.random() - 0.5) * Math.abs(dx) * 0.3;
        var cp2y = y0 + dy * 0.75 + (Math.random() - 0.5) * Math.abs(dy) * 0.3;
        var pts = [];
        for (var i = 0; i <= steps; i++) {
            var t = i / steps;
            var bx = bezier(t, x0, cp1x, cp2x, x1);
            var by = bezier(t, y0, cp1y, cp2y, y1);
            var jx = i === steps ? 0 : gaussRand(0, 2);
            var jy = i === steps ? 0 : gaussRand(0, 2);
            pts.push({
                x: Math.round(bx + jx),
                y: Math.round(by + jy),
                delay: Math.floor(Math.random() * 31) + 20, // 20-50ms
            });
        }
        return pts;
    };
})();
