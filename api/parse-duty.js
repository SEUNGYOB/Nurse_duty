// Vercel serverless function — proxies OCR requests to the Mac Mini server.
// Environment variables (set in Vercel dashboard):
//   OCR_API_URL   : Mac Mini external URL, e.g. https://your-tunnel.trycloudflare.com
//   OCR_API_TOKEN : must match server.py API_TOKEN

export const config = {
  api: {
    bodyParser: false,
    responseLimit: "12mb",
  },
};

async function readStream(stream) {
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks);
}

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  const apiUrl = process.env.OCR_API_URL;
  if (!apiUrl) {
    return res.status(503).json({ error: "OCR 서버 주소가 설정되지 않았습니다." });
  }

  const body = await readStream(req);

  let upstream;
  try {
    upstream = await fetch(`${apiUrl}/api/parse-duty`, {
      method: "POST",
      headers: {
        "content-type": req.headers["content-type"] ?? "",
        "x-api-token": process.env.OCR_API_TOKEN ?? "",
      },
      body,
    });
  } catch (err) {
    console.error("OCR upstream error:", err);
    return res.status(502).json({ error: "OCR 서버에 연결할 수 없습니다." });
  }

  const data = await upstream.json().catch(() => ({ error: "응답 파싱 실패" }));
  return res.status(upstream.status).json(data);
}
