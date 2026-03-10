// Keyboard timing: Gaussian delay mu=80ms, sigma=20ms, clamped [40, 150]ms.
// Exposes a helper on window.__stealth for the executor to query per-key delays.
(function() {
    window.__stealth = window.__stealth || {};

    function gaussianRandom(mean, sigma) {
        let u = 0, v = 0;
        while (u === 0) u = Math.random();
        while (v === 0) v = Math.random();
        const z = Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
        return mean + z * sigma;
    }

    /**
     * Generate an array of keystroke delays for a string of given length.
     * Each delay is Gaussian(mu=80, sigma=20) clamped to [40, 150]ms.
     */
    window.__stealth.generateKeyDelays = function(length) {
        const delays = [];
        for (let i = 0; i < length; i++) {
            let d = gaussianRandom(80, 20);
            d = Math.max(40, Math.min(150, Math.round(d)));
            delays.push(d);
        }
        return delays;
    };
})();
