// Conventional Commits are what release-please parses to pick the next
// version and write CHANGELOG.md. An unprefixed subject is not an error in
// git, it just silently vanishes from the release notes -- hence the lint.
module.exports = {
  extends: ["@commitlint/config-conventional"],
};
