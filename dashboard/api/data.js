// Vercel serverless function: live channel data for the dashboard.
// Env vars (set in Vercel): YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN,
// GROQ_API_KEY (optional), GITHUB_REPO (optional, e.g. "owner/repo").
// Secrets are only used server-side; the response never contains them.

const YT = "https://www.googleapis.com/youtube/v3";
// Values set from a Windows shell can carry a trailing line break; trim them
const env = name => (process.env[name] || "").trim();

// One entry per channel; each has its own saved YouTube login
const CHANNELS = {
  elite: { name: "Elite Mind Decoder", token: "YT_REFRESH_TOKEN", workflow: "autopilot.yml",
           about: "money psychology and mind tricks" },
  headline: { name: "Headline Tales", token: "YT_REFRESH_TOKEN_HEADLINE", workflow: "headline.yml",
              about: "short documentaries about what is happening in the world" },
};

async function accessToken(tokenEnv) {
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: env("YT_CLIENT_ID"),
      client_secret: env("YT_CLIENT_SECRET"),
      refresh_token: env(tokenEnv),
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

async function channelData(ch) {
  const token = await accessToken(ch.token);
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
  const watch = await watchData(token, videos);
  for (const v of videos) Object.assign(v, (watch && watch.perVideo[v.id]) || { watchHours: 0, avgPct: 0 });
  if (watch) delete watch.perVideo;
  const s = channel.statistics;
  return {
    watch,
    channel: { title: channel.snippet.title, subscribers: +s.subscriberCount, views: +s.viewCount, videos: +s.videoCount },
    videos: videos.filter(v => v.privacy === "public").sort((a, b) => b.published.localeCompare(a.published)),
  };
}

// Watch time from YouTube Analytics (lags 2-3 days behind real time).
// YouTube Partner Program counts watch hours of public LONG videos over the last
// 365 days (Shorts don't count there; they need 10M Shorts views in 90 days).
async function watchData(token, videos) {
  const day = n => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);
  const report = async (start, metrics) => {
    const params = { ids: "channel==MINE", startDate: start, endDate: day(0), metrics, dimensions: "video", maxResults: "200", sort: "-views" };
    const res = await fetch("https://youtubeanalytics.googleapis.com/v2/reports?" + new URLSearchParams(params), {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new Error("Analytics " + res.status);
    const r = await res.json();
    const names = r.columnHeaders.map(h => h.name);
    return (r.rows || []).map(row => Object.fromEntries(row.map((v, i) => [names[i], v])));
  };
  try {
    const [year, quarter] = await Promise.all([
      report(day(365), "views,estimatedMinutesWatched,averageViewPercentage"),
      report(day(90), "views"),
    ]);
    const type = Object.fromEntries(videos.map(v => [v.id, v.type]));
    const perVideo = {};
    let longMinutes = 0, allMinutes = 0, shortViews90 = 0;
    for (const r of year) {
      perVideo[r.video] = { watchHours: +(r.estimatedMinutesWatched / 60).toFixed(1), avgPct: +r.averageViewPercentage.toFixed(1) };
      allMinutes += r.estimatedMinutesWatched;
      if (type[r.video] === "long") longMinutes += r.estimatedMinutesWatched;
    }
    for (const r of quarter) if (type[r.video] === "short") shortViews90 += r.views;
    return {
      perVideo,
      totalHours: +(allMinutes / 60).toFixed(1),
      yppLongHours: +(longMinutes / 60).toFixed(1),
      yppShortViews: shortViews90,
    };
  } catch {
    return null;  // dashboard still works without analytics
  }
}

async function githubExtras(ch) {
  const repo = env("GITHUB_REPO");
  if (!repo) return { runs: [], next: {} };
  const raw = name => fetch(`https://raw.githubusercontent.com/${repo}/main/${name}`)
    .then(r => (r.ok ? r.text() : ""))
    .then(t => t.split("\n").map(l => l.split("|")[0].trim()).filter(Boolean))
    .catch(() => []);
  const [runs, series, explainers, long] = await Promise.all([
    fetch(`https://api.github.com/repos/${repo}/actions/workflows/${ch.workflow}/runs?per_page=12`)
      .then(r => (r.ok ? r.json() : { workflow_runs: [] }))
      .then(d => d.workflow_runs.map(r => ({ started: r.run_started_at, event: r.event, status: r.status, conclusion: r.conclusion, url: r.html_url })))
      .catch(() => []),
    ...(ch.workflow === "autopilot.yml" ? [raw("topics_series.txt"), raw("topics.txt"), raw("topics_long.txt")] : [[], [], []]),
  ]);
  return { runs, next: { series: series.slice(0, 5), explainers: explainers.slice(0, 5), long: long.slice(0, 3) } };
}

async function communityIdeas(ch) {
  if (!env("GROQ_API_KEY")) return [];
  try {
    const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: `Bearer ${env("GROQ_API_KEY")}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "openai/gpt-oss-120b",
        reasoning_effort: "low",
        messages: [{
          role: "user",
          content: `Write 3 short YouTube community posts for '${ch.name}', a channel about ${ch.about}. Mix: one poll (question + 3-4 short options), one question that invites comments, one teaser for the next video. Casual, punchy, max 40 words each. Return ONLY a JSON array of 3 strings.`,
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
    const ch = CHANNELS[req.query.ch] || CHANNELS.elite;
    const [yt, extras, ideas] = await Promise.all([channelData(ch), githubExtras(ch), communityIdeas(ch)]);
    // Cache at Vercel's edge for 5 minutes (about 3 quota units per refresh,
    // far inside YouTube's 10,000/day); never serve data older than ~6 minutes
    res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=60");
    res.status(200).json({ updated: new Date().toISOString(), ...yt, ...extras, community: { ideas } });
  } catch (err) {
    res.status(500).json({ error: String(err.message || err) });
  }
};
