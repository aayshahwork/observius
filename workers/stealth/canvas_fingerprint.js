// Canvas fingerprint noise — deterministic per session via injected seed.
// The seed is set by apply_stealth() before this script runs.
(function() {
    const seed = window.__stealth_seed || 42;

    // Simple seeded PRNG (mulberry32)
    function mulberry32(a) {
        return function() {
            a |= 0; a = a + 0x6D2B79F5 | 0;
            var t = Math.imul(a ^ a >>> 15, 1 | a);
            t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
            return ((t ^ t >>> 14) >>> 0) / 4294967296;
        };
    }

    const rng = mulberry32(seed);

    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        const ctx = this.getContext('2d');
        if (ctx) {
            const imageData = ctx.getImageData(0, 0, this.width, this.height);
            const data = imageData.data;
            // Apply subtle noise to a small subset of pixels
            for (let i = 0; i < data.length; i += 4 * 13) {
                data[i] = (data[i] + Math.floor(rng() * 3) - 1) & 0xff;
            }
            ctx.putImageData(imageData, 0, 0);
        }
        return origToDataURL.apply(this, arguments);
    };

    const origToBlob = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {
        const ctx = this.getContext('2d');
        if (ctx) {
            const imageData = ctx.getImageData(0, 0, this.width, this.height);
            const data = imageData.data;
            for (let i = 0; i < data.length; i += 4 * 13) {
                data[i] = (data[i] + Math.floor(rng() * 3) - 1) & 0xff;
            }
            ctx.putImageData(imageData, 0, 0);
        }
        return origToBlob.apply(this, arguments);
    };
})();
