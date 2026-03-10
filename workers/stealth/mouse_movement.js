// Mouse movement: Bezier curves with 10-20 steps, 20-50ms intervals, Gaussian jitter sigma=2px.
// This script exposes a helper on window.__stealth that the executor calls via page.evaluate().
(function() {
    window.__stealth = window.__stealth || {};

    // Box-Muller transform for Gaussian random
    function gaussianRandom(mean, sigma) {
        let u = 0, v = 0;
        while (u === 0) u = Math.random();
        while (v === 0) v = Math.random();
        const z = Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
        return mean + z * sigma;
    }

    // Cubic Bezier interpolation
    function bezierPoint(t, p0, p1, p2, p3) {
        const u = 1 - t;
        return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3;
    }

    /**
     * Generate a Bezier curve path from (x0, y0) to (x1, y1).
     * Returns an array of {x, y, delay} objects.
     */
    window.__stealth.generateMousePath = function(x0, y0, x1, y1) {
        const steps = Math.floor(Math.random() * 11) + 10; // 10-20 steps
        const points = [];

        // Control points with some randomness
        const dx = x1 - x0;
        const dy = y1 - y0;
        const cp1x = x0 + dx * 0.25 + (Math.random() - 0.5) * Math.abs(dx) * 0.3;
        const cp1y = y0 + dy * 0.25 + (Math.random() - 0.5) * Math.abs(dy) * 0.3;
        const cp2x = x0 + dx * 0.75 + (Math.random() - 0.5) * Math.abs(dx) * 0.3;
        const cp2y = y0 + dy * 0.75 + (Math.random() - 0.5) * Math.abs(dy) * 0.3;

        for (let i = 0; i <= steps; i++) {
            const t = i / steps;
            const bx = bezierPoint(t, x0, cp1x, cp2x, x1);
            const by = bezierPoint(t, y0, cp1y, cp2y, y1);

            // Add Gaussian jitter (sigma=2px)
            const jitterX = i === steps ? 0 : gaussianRandom(0, 2);
            const jitterY = i === steps ? 0 : gaussianRandom(0, 2);

            // Delay between 20-50ms
            const delay = Math.floor(Math.random() * 31) + 20;

            points.push({
                x: Math.round(bx + jitterX),
                y: Math.round(by + jitterY),
                delay: delay,
            });
        }

        return points;
    };
})();
