// Patch chrome.runtime, chrome.csi, chrome.loadTimes
if (!window.chrome) {
    window.chrome = {};
}

window.chrome.runtime = window.chrome.runtime || {
    onConnect: { addListener: function() {}, removeListener: function() {} },
    onMessage: { addListener: function() {}, removeListener: function() {} },
    sendMessage: function() {},
    connect: function() {
        return {
            onMessage: { addListener: function() {} },
            postMessage: function() {},
            disconnect: function() {},
        };
    },
    id: undefined,
};

window.chrome.csi = window.chrome.csi || function() {
    return {
        startE: Date.now(),
        onloadT: Date.now(),
        pageT: Math.random() * 1000 + 500,
        tran: 15,
    };
};

window.chrome.loadTimes = window.chrome.loadTimes || function() {
    return {
        commitLoadTime: Date.now() / 1000,
        connectionInfo: 'h2',
        finishDocumentLoadTime: Date.now() / 1000,
        finishLoadTime: Date.now() / 1000,
        firstPaintAfterLoadTime: 0,
        firstPaintTime: Date.now() / 1000,
        navigationType: 'Other',
        npnNegotiatedProtocol: 'h2',
        requestTime: Date.now() / 1000 - 0.16,
        startLoadTime: Date.now() / 1000 - 0.3,
        wasAlternateProtocolAvailable: false,
        wasFetchedViaSpdy: true,
        wasNpnNegotiated: true,
    };
};
