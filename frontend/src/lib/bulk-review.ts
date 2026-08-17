/**
 * Sequential, continue-on-error batch runner for request review.
 *
 * Pure: no React, no fetch. Runs `perItem` over `ids` strictly one at a time
 * (never in parallel — parallelizing the slow budget applies does not help and
 * risks racing on the same member's active-boost row). A thrown `perItem` is
 * captured as `{ ok: false, error }` and the loop continues, so one bad item
 * never aborts the batch (partial success is the intended outcome).
 */

export interface BulkItemResult {
  id: string;
  /** false when `perItem` threw. */
  ok: boolean;
  /** server `detail` message (or stringified error) when `ok === false`. */
  error?: string;
}

export interface BulkProgress {
  total: number;
  /** items processed so far (succeeded + failed). */
  done: number;
  results: BulkItemResult[];
}

export async function runSequential(
  ids: string[],
  perItem: (id: string) => Promise<void>,
  onProgress?: (p: BulkProgress) => void,
): Promise<BulkItemResult[]> {
  const results: BulkItemResult[] = [];
  for (const id of ids) {
    try {
      await perItem(id);
      results.push({ id, ok: true });
    } catch (e) {
      results.push({
        id,
        ok: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
    onProgress?.({ total: ids.length, done: results.length, results: [...results] });
  }
  return results;
}
