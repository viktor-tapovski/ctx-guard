// Conventional Commits are what release-please parses to pick the next
// version and write CHANGELOG.md. An unprefixed subject is not an error in
// git, it just silently vanishes from the release notes -- hence the lint.
module.exports = {
  extends: ["@commitlint/config-conventional"],
  rules: {
    // config-conventional bans sentence-case subjects outright, which here
    // means a subject may not start with an acronym -- CI, ASCII, TTY, JSON
    // and MEASURED all come up in this codebase. Keep the rule for the cases
    // that are genuinely sloppy and drop sentence-case from the list.
    "subject-case": [2, "never", ["start-case", "pascal-case", "upper-case"]],
  },
};
