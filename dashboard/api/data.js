// Vercel serverless function: live channel data for the dashboard.
// Env vars (set in Vercel): YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN,
// GROQ_API_KEY (optional), GITHUB_REPO (optional, e.g. "owner/repo").
// Secrets are only used server-side; the response never contains them.

const YT = "https://www.googleapis.com/youtube/v3";

async function accessToken() {
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: process.env.YT_CLIENT_ID,
      client_secret: process.env.YT_CLIENT_SECRET,
      refresh_token: process.env.YT_REFRESH_TOKEN,
      grant_type: "refresh_token",
    }),
  });
  if (!res.ok) throw new Error("YouTube login failed (" + res.status + ")");
  return (await res.json()).access_token;
}

async function yt(path, params, token) {
  const res = await fetch(`${YT}/${path}?${new URLSearchParams(params)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`YouTube ${path} failed (${res.status})`);
  return res.json();
}

function seconds(duration) {
  const m = /PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/.exec(duration || "") || [];
  return (+m[1] || 0) * 3600 + (+m[2] || 0) * 60 + (+m[3] || 0);
}

async function channelData() {
  const token = await accessToken();
  const channel = (await yt("channels", { part: "snippet,statistics,contentDetails", mine: "true" }, token)).items[0];
  const uploads = channel.contentDetails.relatedPlaylists.uploads;

  let ids = [], page = "";
  do {
    const r = await yt("playlistItems", { part: "contentDetails", playlistId: uploads, maxResults: "50", ...(page && { pageToken: page }) }, token);
    ids = ids.concat(r.items.map(i => i.contentDetails.videoId));
    page = r.nextPageToken || "";
  } while (page && ids.length < 200);

  const videos = [];
  for (let i = 0; i < ids.length; i += 50) {
    const r = await yt("videos", { part: "snippet,statistics,contentDetails,status", id: ids.slice(i, i + 50).join(",") }, token);
    for (const v of r.items) {
      const secs = seconds(v.contentDetails.duration);
      videos.push({
        id: v.id,
        title: v.snippet.title,
        published: v.snippet.publishedAt,
        thumbnail: (v.snippet.thumbnails.medium || {}).url || "",
        seconds: secs,
        type: secs <= 180 ? "short" : "long",
        privacy: v.status.privacyStatus,
        views: +(v.statistics.viewCount || 0),
        likes: +(v.statistics.likeCount || 0),
        comments: +(v.statistics.commentCount || 0),
      });
    }
  }
  const s = channel.statistics;
  return {
    channel: { title: channel.snippet.title, subscribers: +s.subscriberCount, views: +s.viewCount, videos: +s.videoCount },
    videos: videos.filter(v => v.privacy === "public").sort((a, b) => b.published.localeCompare(a.published)),
  };
}

async function githubExtras() {
  const repo = process.env.GITHUB_REPO;
  if (!repo) return { runs: [], next: {} };
  const raw = name => fetch(`https://raw.githubusercontent.com/${repo}/main/${name}`)
    .then(r => (r.ok ? r.text() : ""))
    .then(t => t.split("\n").map(l => l.split("|")[0].trim()).filter(Boolean))
    .catch(() => []);
  const [runs, series, explainers, long] = await Promise.all([
    fetch(`https://api.github.com/repos/${repo}/actions/workflows/autopilot.yml/runs?per_page=12`)
      .then(r => (r.ok ? r.json() : { workflow_runs: [] }))
      .then(d => d.workflow_runs.map(r => ({ started: r.run_started_at, event: r.event, status: r.status, conclusion: r.conclusion, url: r.html_url })))
      .catch(() => []),
    raw("topics_series.txt"), raw("topics.txt"), raw("topics_long.txt"),
  ]);
  return { runs, next: { series: series.slice(0, 5), explainers: explainers.slice(0, 5), long: long.slice(0, 3) } };
}

async function communityIdeas() {
  if (!process.env.GROQ_API_KEY) return [];
  try {
    const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: `Bearer ${process.env.GROQ_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "openai/gpt-oss-120b",
        reasoning_effort: "low",
        messages: [{
          role: "user",
          content: "Write 3 short YouTube community posts for 'Elite Mind Decoder', a channel about money psychology and mind tricks. Mix: one poll (question + 3-4 short options), one question that invites comments, one teaser for the next video. Casual, punchy, max 40 words each. Return ONLY a JSON array of 3 strings.",
        }],
      }),
    });
    const text = (await res.json()).choices[0].message.content;
    return JSON.parse(text.match(/\[[\s\S]*\]/)[0]).slice(0, 3);
  } catch {
    return [];
  }
}

module.exports = async (req, res) => {
  try {
    const [yt, extras, ideas] = await Promise.all([channelData(), githubExtras(), communityIdeas()]);
    // Cache at Vercel's edge for 15 minutes to stay well inside YouTube's API quota
    res.setHeader("Cache-Control", "s-maxage=900, stale-while-revalidate=3600");
    res.status(200).json({ updated: new Date().toISOString(), ...yt, ...extras, community: { ideas } });
  } catch (err) {
    res.status(500).json({ error: String(err.message || err) });
  }
};
