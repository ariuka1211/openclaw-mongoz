const Store = {
  _cache: {},
  _listeners: {},
  _timestamps: {},

  get(key) {
    return this._cache[key];
  },

  set(key, value) {
    this._cache[key] = value;
    this._timestamps[key] = new Date();
    (this._listeners[key] || []).forEach(fn => fn(value));
  },

  subscribe(key, fn) {
    (this._listeners[key] ||= []).push(fn);
    if (this._cache[key] !== undefined) fn(this._cache[key]);
  },

  lastUpdated(key) {
    return this._timestamps[key] || null;
  }
};
