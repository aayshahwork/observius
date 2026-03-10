// Remove navigator.webdriver flag
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
});
