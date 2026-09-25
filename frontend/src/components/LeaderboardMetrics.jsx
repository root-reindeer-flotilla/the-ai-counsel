import { formatUsd } from '../utils/formatCost';

/*
 * Fork: per-model generation time, tokens and cost in the Stage 2 leaderboard
 * (aggregate ranking rows from backend/runs.py). Renders nothing for rows
 * without these fields, such as upstream streams and old conversations.
 */
export default function LeaderboardMetrics({ agg }) {
    const parsedRowSeconds = Number(agg?.generation_time_seconds);
    const safeRowSeconds = agg?.generation_time_seconds != null && Number.isFinite(parsedRowSeconds)
        ? Math.max(0, Math.round(parsedRowSeconds))
        : null;
    const generationTimeLabel = safeRowSeconds !== null ? `${safeRowSeconds}s` : null;
    const parsedRowTokens = Number(agg?.generation_total_tokens);
    const safeRowTokens = agg?.generation_total_tokens != null && Number.isFinite(parsedRowTokens)
        ? Math.max(0, Math.round(parsedRowTokens))
        : null;
    const generationTokensLabel = safeRowTokens !== null
        ? safeRowTokens.toLocaleString('en-US')
        : null;
    const generationCostLabel = typeof agg?.generation_total_cost === 'number'
        ? formatUsd(agg.generation_total_cost)
        : null;

    return (
        <>
            {generationTimeLabel && (
                <span className="rank-score" title="Generation time (Stages 1 and 2)">
                    {" | "}
                    {generationTimeLabel}
                </span>
            )}
            {generationTokensLabel && (
                <span className="rank-score" title="Tokens used (Stages 1 and 2)">
                    {" | "}
                    {generationTokensLabel}
                </span>
            )}
            {generationCostLabel && (
                <span className="rank-score" title="Cost (Stages 1 and 2)">
                    {" | "}
                    {generationCostLabel}
                </span>
            )}
        </>
    );
}
