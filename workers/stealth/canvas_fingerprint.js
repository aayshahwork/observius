// Canvas fingerprint noise — deterministic per session via __stealth_seed.
(function () {
    var seed = window.__stealth_seed || 42;

    // Mulberry32 PRNG.
    function mulberry32(a) {
        return function () {
            a |= 0;
            a = (a + 0x6d2b79f5) | 0;
            var t = Math.imul(a ^ (a >>> 15), 1 | a);
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    var rng = mulberry32(seed);

    var origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function () {
        var ctx = this.getContext('2d');
        if (ctx) {
            try {
                var imageData = ctx.getImageData(0, 0, this.width, this.height);
                var d = imageData.data;
                for (var i = 0; i < d.length; i += 52) {
                    d[i] = (d[i] + Math.floor(rng() * 3) - 1) & 0xff;
                }
                ctx.putImageData(imageData, 0, 0);
            } catch (e) {}
        }
        return origToDataURL.apply(this, arguments);
    };

    var origToBlob = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function () {
        var ctx = this.getContext('2d');
        if (ctx) {
            try {
                var imageData = ctx.getImageData(0, 0, this.width, this.height);
                var d = imageData.data;
                for (var i = 0; i < d.length; i += 52) {
                    d[i] = (d[i] + Math.floor(rng() * 3) - 1) & 0xff;
                }
                ctx.putImageData(imageData, 0, 0);
            } catch (e) {}
        }
        return origToBlob.apply(this, arguments);
    };
})();
