// Vercel Cron -> GitHub Actions: GitHub's own scheduler drops runs, so Vercel's
// cron (see vercel.json) starts each posting slot with a workflow_dispatch.
// Env (Vercel): GITHUB_DISPATCH_TOKEN (fine-grained token, Actions: read & write on
// the repo), GITHUB_REPO, CRON_SECRET (Vercel sends it as a Bearer token).

const env = name => (process.env[name] || "").trim();

// slot -> workflow file + inputs. Vercel Hobby cron fires anywhere within the hour,
// which already varies posting times, so no extra random delay.
// Slots follow the channel's best launch windows (YouTube Studio): ~10:00 and ~22:30 WAT.
const JOBS = {
  "elite-morning": ["autopilot.yml", { format: "short", series: "false", random_delay: "false" }],
  "elite-midday": ["autopilot.yml", { format: "short", series: "false", random_delay: "false" }],
  "elite-evening": ["autopilot.yml", { format: "short", series: "true", random_delay: "false" }],
  "elite-extra": ["autopilot.yml", { format: "short", series: "false", random_delay: "false" }],
  "elite-long": ["autopilot.yml", { format: "long", series: "false", random_delay: "false" }],
  "headline-full": ["headline.yml", { mode: "full" }],
  "headline-short": ["headline.yml", { mode: "short" }],
  "research": ["research.yml", {}],
  "compilation": ["compilation.yml", {}],
};

module.exports = async (req, res) => {
  if (env("CRON_SECRET") && req.headers.authorization !== `Bearer ${env("CRON_SECRET")}`) {
    return res.status(401).json({ status: "error", error: "unauthorized" });
  }
  const job = JOBS[req.query.job];
  if (!job) return res.status(400).json({ status: "error", error: "unknown job" });
  const [workflow, inputs] = job;
  const r = await fetch(`https://api.github.com/repos/${env("GITHUB_REPO")}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env("GITHUB_DISPATCH_TOKEN")}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref: "main", inputs }),
  });
  if (!r.ok) return res.status(502).json({ status: "error", error: `GitHub ${r.status}: ${(await r.text()).slice(0, 200)}` });
  res.status(200).json({ status: "success", job: req.query.job, workflow });
};
