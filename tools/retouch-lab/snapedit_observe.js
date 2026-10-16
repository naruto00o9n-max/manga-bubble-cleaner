/* Observe this test device's requests. Do not change requests, responses, TLS,
 * membership checks, signatures, or integrity results. Raw data is sealed by
 * the Python controller before leaving the disposable runner. */
Java.perform(function () {
  const Modifier = Java.use('java.lang.reflect.Modifier');
  const ArrayReflect = Java.use('java.lang.reflect.Array');
  const Base64 = Java.use('android.util.Base64');
  const Body = Java.use('okhttp3.RequestBody');
  const Buffer = Java.use('Pd.j'); // Okio Buffer in the inspected 7.8 release.
  const Server = Java.use('okhttp3.internal.http.CallServerInterceptor');
  let nextId = 0;
  function field(obj, wanted) {
    const fields = obj.getClass().getDeclaredFields();
    for (let i = 0; i < fields.length; i++) {
      const f = fields[i];
      if (!Modifier.isStatic(f.getModifiers()) && String(f.getType().getName()) === wanted) {
        f.setAccessible(true);
        return f.get(obj);
      }
    }
    return null;
  }
  function headers(obj) {
    const h = field(obj, 'okhttp3.Headers');
    const values = h ? field(h, '[Ljava.lang.String;') : null;
    const out = [];
    if (values) for (let i = 0; i + 1 < ArrayReflect.getLength(values); i += 2)
      out.push([String(ArrayReflect.get(values, i)), String(ArrayReflect.get(values, i + 1))]);
    return out;
  }
  const original = Server.intercept.overload('okhttp3.Interceptor$Chain');
  original.implementation = function (chain) {
    let id = null;
    try {
      const req = field(chain, 'okhttp3.Request');
      const url = String(field(req, 'okhttp3.HttpUrl'));
      if (/^https:\/\/[a-z0-9-]+\.snapedit\.app\//i.test(url)) {
        id = ++nextId;
        const event = {kind: 'request', id: id, at_ms: Date.now(), url: url,
          method: String(field(req, 'java.lang.String')), headers: headers(req)};
        const raw = field(req, 'okhttp3.RequestBody');
        if (raw) {
          const body = Java.cast(raw, Body);
          event.body_type = String(body.contentType());
          event.body_length = Number(body.contentLength());
          if (!body.isOneShot() && !body.isDuplex() && event.body_length >= 0 && event.body_length <= 20 * 1024 * 1024) {
            const copy = Buffer.$new();
            body.writeTo(copy);
            event.body_b64 = String(Base64.encodeToString(copy.V(), 2));
          }
        }
        send(event);
      }
    } catch (_) { send({kind: 'observer_error', stage: 'request'}); }
    // The original network call receives exactly its original chain.
    try {
      const response = original.call(this, chain);
      if (id !== null) {
        try { send({kind: 'response', id: id, at_ms: Date.now(),
          status: Number(String(field(response, 'int'))), headers: headers(response)}); }
        catch (_) { send({kind: 'observer_error', stage: 'response'}); }
      }
      return response;
    } catch (error) {
      if (id !== null) send({kind: 'network_error', id: id, at_ms: Date.now()});
      throw error;
    }
  };
  send({kind: 'ready', at_ms: Date.now()});
});
