/**
 * Return the default semantic display names used for the three general reviewers.
 */
const DEFAULT_REVIEWER_NAMES = ["Architecture", "Backend", "Frontend"];

export function generateReviewerNames(count = 3) {
  return DEFAULT_REVIEWER_NAMES.slice(0, count);
}
