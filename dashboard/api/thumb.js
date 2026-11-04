// Owner thumbnail upload: the phone page (thumb.html) sends a 1280x720 JPEG; this saves
// it to thumbnails/<channel>_<date>.jpg in the repo, where the long-video run picks it up
// just before posting. No password (owner's choice), so only today's or tomorrow's
// thumbnail can be set: videos that already posted can't be changed from here.
// Env (Vercel): GITHUB_REPO, GITHUB_CONTENTS_TOKEN (fine-grained, Contents: read & write)
// or GITHUB_DISPATCH_TOKEN if that one has Contents access too.

const env = name => (process.env[name] || "").trim();

module.exports = async (req, res) => {
  if (req.method !== "POST") return res.status(405).json({ status: "error", error: "POST only" });
  const { channel, date, image } = req.body || {};
  if (!["headline", "elite"].includes(channel) || !/^\d{4}-\d{2}-\d{2}$/.test(date || "")) {
    return res.status(400).json({ status: "error", error: "Pick a channel and a date" });
  }
  const day = iso => new Date(Date.now() + iso * 86400e3).toISOString().slice(0, 10);
  if (![day(-1), day(0), day(1)].includes(date)) {  // -1 covers time zones around midnight
    return res.status(400).json({ status: "error", error: "Only today's or tomorrow's thumbnail can be uploaded" });
  }
  const content = String(image || "").replace(/^data:image\/\w+;base64,/, "");
  if (content.length < 1000 || content.length > 3_500_000) {
    return res.status(400).json({ status: "error", error: "Image missing or too big" });
  }

  const token = env("GITHUB_CONTENTS_TOKEN") || env("GITHUB_DISPATCH_TOKEN");
  const url = `https://api.github.com/repos/${env("GITHUB_REPO")}/contents/thumbnails/${channel}_${date}.jpg`;
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  // Replacing an earlier upload for the same day needs its sha
  const existing = await fetch(`${url}?ref=main`, { headers });
  const sha = existing.ok ? (await existing.json()).sha : undefined;
  const r = await fetch(url, {
    method: "PUT",
    headers,
    body: JSON.stringify({ message: `thumbnail: ${channel} ${date}`, content, branch: "main", ...(sha && { sha }) }),
  });
  if (!r.ok) return res.status(502).json({ status: "error", error: `GitHub ${r.status}: ${(await r.text()).slice(0, 200)}` });
  res.status(200).json({ status: "success", file: `thumbnails/${channel}_${date}.jpg` });
};
