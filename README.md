# Simple Voting Contract with Consensus

A GenLayer intelligent contract for conducting polls and votes with legitimacy verification through LLM consensus.

## Deployment

|                        |                                                                      |
| ---------------------- | -------------------------------------------------------------------- |
| Network                | Studionet                                                            |
| Contract address       | `0xf3B1934cC8BBBD10dD7c6b12322cFD53906f9916`                         |
| Deployment transaction | `0x901a1619112c1aa711acaeb04b31e4a1748233b2584e680d6eb0e02357f215ec` |
| Status                 | FINALIZED, consensus Accepted                                        |

## Features

- Create a voting question with multiple options
- Users can vote with reasoning for their choice
- Prevents double voting
- Uses LLM consensus to assess vote legitimacy (detects coercion, manipulation, etc.)
- Only counts votes deemed legitimate by consensus (≥70/100 legitimacy score)
- Owner can end voting period once it has elapsed (or it ends automatically at the deadline)
- Results become readable automatically once the deadline passes (or the owner ends voting)
- Timeout-based voting period (wall-clock seconds), enforced at vote time: `vote_with_reasoning`
  rejects any vote cast after `end_timestamp`, even before the owner calls `end_voting`
- Live tallies are hidden while voting is open: `get_results`/`get_current_tallies` revert and
  `get_options` omits vote counts until voting has ended, so early counts cannot influence voters
- Reasoning and option text are validated: empty reasoning or empty option text is rejected
- Full audit trail of vote reasoning and legitimacy assessments

## Consensus Mechanism

This contract leverages GenLayer's unique consensus capabilities:

1. **Vote Legitimacy Assessment**: When voting, users must provide reasoning for their choice
2. **LLM Analysis**: The reasoning is analyzed by an LLM to detect signs of:
   - Coercion or manipulation
   - Lack of genuine preference
   - Vote buying or improper influence
3. **Score-Derived Legitimacy**: The LLM returns only a `score` (0-100) and `notes`. The
   contract normalizes the score (clamped to 0-100, unparseable values fall back to 50) and
   **derives** the legitimacy flag from it via `LEGITIMACY_THRESHOLD`. The model's own
   opinion about legitimacy is never read, so the score and the flag cannot disagree.
4. **Validator Agreement**: Multiple validators independently run the same LLM analysis
5. **Equivalence Principle**: A leader result is accepted only if:
   - Its `(score, flag)` pair is self-consistent — a leader whose flag contradicts its own
     score is rejected outright, before any comparison
   - Leader and validator scores agree within a 15-point tolerance
   - Leader and validator legitimacy decisions match exactly
   - Explanations overlap by at least 30% of their combined vocabulary
6. **Selective Counting**: Only votes scoring ≥70/100 in legitimacy are counted toward the final tally

The threshold is defined once, at module level:

```python
LEGITIMACY_THRESHOLD = 70   # voting.py
```

Both the leader path and the validator path derive the flag from it, and `vote_with_reasoning`
re-derives it again before writing storage, so the stored record and the tally can never
contradict the rule.

## Contract Details

### Storage

- `owner`: Address of contract creator (the deploy-time sender)
- `question`: The voting question
- `options`: `TreeMap[str, VoteOption]` of vote options with descriptions and vote counts (only legitimate votes)
- `voters`: `TreeMap[str, VoteRecord]` keyed by `str(voter_address)` → their complete vote record
- `voting_ended`: Boolean flag the owner sets by calling `end_voting()`; voting is also
  considered ended (for status, results, and vote gating) once `end_timestamp` passes
- `end_timestamp`: Unix timestamp (seconds) at which the voting period elapses

### VoteOption Dataclass

- `option_id`: Generated ID (`opt_0`, `opt_1`, …)
- `description`: Option text supplied at deployment
- `votes`: Count of legitimate votes for this option

### VoteRecord Dataclass

Each vote record stores:

- `voter_addr`: Address of the voter
- `option_id`: Selected option ID
- `reasoning`: User-provided reasoning for their vote
- `legitimacy_score`: 0-100 scale from LLM consensus
- `is_legitimate`: Boolean, derived as `score >= 70`
- `timestamp`: When the vote was cast (unix seconds, as a string)

### Functions

#### Write Functions

- `vote_with_reasoning(option_id: str, reasoning: str)`: Cast a vote with reasoning (uses `gl.message.sender_address`); rejects empty reasoning, and fails once the configured deadline (`end_timestamp`) has passed, even if `end_voting()` was never called
- `end_voting()`: Owner-only; fails until `end_timestamp` has passed

#### View Functions

- `get_results()`: Get voting results (only after voting has ended — deadline passed or `end_voting` called)
- `get_current_tallies()`: Get final tallies (only after voting has ended; hidden while voting is open)
- `get_options()`: Get all options with their descriptions (always available, so voters can map
  option IDs); vote counts are included only once voting has ended
- `get_question()`: Get the voting question
- `has_voted(voter_addr: Address)`: Check if an address has voted
- `get_vote_record(voter_addr: Address)`: Get complete vote record including legitimacy assessment
- `get_all_vote_records()`: Get every vote record (full audit trail)
- `get_voting_status()`: Get `ended` (True once the deadline passes or `end_voting` is called),
  `current_timestamp`, `end_timestamp`, `time_remaining`, and `owner`

## Usage

1. Deploy the contract with a question, options, and an optional duration in **seconds**
   (default 604800 = 7 days):

   ```python
   contract = VotingContract("Best programming language?", ["Python", "JavaScript", "Go"], 604800)
   ```

2. Users vote by calling `vote_with_reasoning()` with option ID and reasoning:

   ```python
   # Users must provide reasoning for their vote
   contract.vote_with_reasoning("opt_0", "I prefer Python for its simplicity and readability")
   ```

3. Owner ends voting with `end_voting()` once `end_timestamp` has passed

4. Results can be viewed with `get_results()` once voting has ended (only counts legitimate
   votes). Use `get_current_tallies()` to inspect final counts. Voting ends when the owner
   calls `end_voting()` *or* automatically when `end_timestamp` passes, so results appear
   even if the owner never ends the poll manually.

5. Individual vote records can be inspected with `get_vote_record()`, or all of them with
   `get_all_vote_records()`, to see reasoning and legitimacy scores

## Security & Design Notes

- **Authentication**: Uses `gl.message.sender_address` for voter identity — prevents voting on behalf of others
- **Single Source of Truth**: `LEGITIMACY_THRESHOLD` (module level) is the only definition of
  legitimacy. The leader derives the flag from the score, the validator rejects any leader
  whose pair is inconsistent, and the write path re-derives before storing.
- **Score Tolerance**: ±15 point tolerance allows for reasonable variation in subjective LLM scoring
- **Decision Exactness**: Legitimacy boolean must match exactly between leader and validator
- **Notes Similarity**: 30% word overlap ensures validators are assessing similar aspects
- **Error Classification**: Errors are tagged `[EXPECTED]`, `[EXTERNAL]`, `[TRANSIENT]`, or
  `[LLM_ERROR]` so validators can distinguish deterministic disagreement from transient failure
- **Deadline Is Authoritative**: `vote_with_reasoning` rejects votes once `end_timestamp` passes,
  and every read path (`get_results`, `get_current_tallies`, `get_voting_status`) treats the poll
  as ended at the deadline even if the owner never calls `end_voting()`. Result views stay hidden
  until voting has actually ended.

## Testing

Direct mode tests live in `tests/direct/`:

- `test_voting.py` — end-to-end contract behavior: deployment validation, voting, double-vote
  prevention, ownership, the voting-period and voting-ended guards, deadline enforcement inside
  `vote_with_reasoning` (including exact-boundary semantics), automatic end at the deadline,
  hidden-until-ended result views, and result access
- `test_legitimacy_invariant.py` — pins the score/flag invariant using mocked LLM replies,
  including adversarial pairs such as `{"score": 85, "legitimate": false}`, threshold
  boundaries, clamping, and validator rejection of inconsistent leader results
- `test_end_to_end.py` — full lifecycle (deploy → mixed legitimate/coerced votes → deadline →
  results), owner-vs-non-owner `end_voting`, hidden-until-ended counts on every read path,
  empty reasoning/option validation, the ±15 score-tolerance and 30% notes-overlap consensus
  boundaries, and vote-record timestamp sanity
- `conftest.py` — Windows-only shim for a gltest bug that unlinks a still-open temp file
  during contract load; a no-op on other platforms

Run the tests with:

```bash
python -m pytest tests/direct -v
```

Run them from the repository root — `direct_deploy("voting.py", …)` resolves the contract
path relative to the current working directory.

## GenLayer Best Practices Followed

- ✅ Pinned runner version: `# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }`
- ✅ Proper storage types: `Address`, `TreeMap[K,V]`, `u256`
- ✅ Correct contract structure with class-level annotations
- ✅ Meaningful consensus usage for subjective judgment (vote legitimacy)
- ✅ Deterministic contract-side rules; the LLM supplies evidence, not decisions
- ✅ Proper error handling with classifications
- ✅ Storage rules followed (no `TreeMap()` assignment in `__init__`)
- ✅ Clear separation of view/write functions
