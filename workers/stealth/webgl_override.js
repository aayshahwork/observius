// WebGL renderer/vendor override to return realistic desktop GPU strings.
(function () {
    var VENDOR = 'Google Inc. (NVIDIA)';
    var RENDERER =
        'ANGLE (NVIDIA, NVIDIA GeForce GTX 1080 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)';

    function patchCtx(proto) {
        var orig = proto.getParameter;
        proto.getParameter = function (param) {
            if (param === 0x9245) return VENDOR; // UNMASKED_VENDOR_WEBGL
            if (param === 0x9246) return RENDERER; // UNMASKED_RENDERER_WEBGL
            return orig.apply(this, arguments);
        };
    }

    patchCtx(WebGLRenderingContext.prototype);
    if (typeof WebGL2RenderingContext !== 'undefined') {
        patchCtx(WebGL2RenderingContext.prototype);
    }
})();
