// Sprechtrainer pronunciation-scoring proxy (Azure Functions, Node.js, v4 programming model).
// Paste this into the portal editor of an HTTP-triggered function named "assess".
//
// App settings (Function App → Settings → Environment variables):
//   SPEECH_KEY     Azure Speech resource key       (secret)
//   SPEECH_REGION  e.g. japaneast
//   PASSPHRASE     any phrase; the app sends it in the x-passphrase header
// CORS (Function App → API → CORS): allow https://esaulgd.github.io
//
// Request:  POST /api/assess?text=<reference text>   body: 16 kHz mono 16-bit WAV
// Response: Azure's pronunciation-assessment JSON (de-DE), passed through unchanged.

const { app } = require("@azure/functions");

const MAX_BYTES = 1_200_000;   // ~37 s of 16 kHz audio; Azure scores at most 30 s
const MAX_TEXT = 300;

app.http("assess", {
  methods: ["POST"],
  authLevel: "anonymous",
  handler: async (request, context) => {
    const json = (status, obj) => ({ status, jsonBody: obj });

    const pass = process.env.PASSPHRASE || "";
    if (!pass || request.headers.get("x-passphrase") !== pass) return json(401, { error: "wrong passphrase" });

    const key = process.env.SPEECH_KEY, region = process.env.SPEECH_REGION || "japaneast";
    if (!key) return json(500, { error: "SPEECH_KEY is not set" });

    const text = (request.query.get("text") || "").trim();
    if (!text || text.length > MAX_TEXT) return json(400, { error: "missing or too long text" });

    const audio = Buffer.from(await request.arrayBuffer());
    if (audio.length < 1000 || audio.length > MAX_BYTES) return json(400, { error: "audio too short or too long" });

    const params = {
      ReferenceText: text,
      GradingSystem: "HundredMark",
      Granularity: "Phoneme",
      Dimension: "Comprehensive",
      EnableMiscue: true
    };
    const url = `https://${region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1` +
                `?language=de-DE&format=detailed`;
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Ocp-Apim-Subscription-Key": key,
          "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
          "Accept": "application/json",
          "Pronunciation-Assessment": Buffer.from(JSON.stringify(params), "utf8").toString("base64")
        },
        body: audio
      });
      const body = await res.text();
      if (!res.ok) context.warn(`Azure returned ${res.status}`);
      return { status: res.ok ? 200 : 502, headers: { "Content-Type": "application/json" },
               body: res.ok ? body : JSON.stringify({ error: `Azure returned ${res.status}`, detail: body.slice(0, 300) }) };
    } catch (e) {
      context.error(e);
      return json(502, { error: "could not reach Azure Speech" });
    }
  }
});
