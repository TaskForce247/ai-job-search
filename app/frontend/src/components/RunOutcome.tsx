import type { Run } from '../types';
import { Badge } from './ui';

export function isDismissedReview(run: Run) {
  return run.kind === 'documents' && (run.result?.outcome === 'review_dismissed' ||
    run.result?.declined === true || (run.status === 'succeeded' &&
      run.approvals.some((approval) => approval.status === 'rejected') &&
      !run.approvals.some((approval) => approval.status === 'approved')));
}

export function RunOutcome({ run }: { run: Run }) {
  return isDismissedReview(run)
    ? <Badge value="pending">Review dismissed · documents unchanged</Badge>
    : <Badge value={run.status} />;
}
