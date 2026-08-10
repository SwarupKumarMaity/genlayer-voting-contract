# Simple Voting Contract with Consensus

A GenLayer intelligent contract for conducting polls and votes with legitimacy verification through LLM consensus.

## Features

- Create a voting question with multiple options
- Users can vote with reasoning for their choice
- Prevents double voting
- Uses LLM consensus to assess vote legitimacy (detects coercion, manipulation, etc.)
- Only counts votes deemed legitimate by consensus (≥70/100 legitimacy score)
- Owner can end voting period
- View results after voting ends
- Timeout-based voting period
- Full audit trail of vote reasoning and legitimacy assessments

## Consensus Mechanism

This contract leverages GenLayer's unique consensus capabilities:

1. **Vote Legitimacy Assessment**: When voting, users must provide reasoning for their choice
2. **LLM Analysis**: The reasoning is analyzed by an LLM to detect signs of:
   - Coercion or manipulation
   - Lack of genuine preference
   - Vote buying or improper influence
3. **Validator Agreement**: Multiple validators independently run the same LLM analysis
4. **Equivalence Principle**: Validators must agree on:
   - Legitimacy scores (within 15-point tolerance)
   - Legitimacy boolean decisions (exact match)
   - Reasoning similarity (30% word overlap in explanations)
5. **Selective Counting**: Only votes scoring ≥70/100 in legitimacy are counted toward the final tally

## Contract Details

### Storage
- `owner`: Address of contract creator
- `question`: The voting question
- `options`: TreeMap of vote options with descriptions and vote counts (only legitimate votes)
- `voters`: TreeMap[Address, VoteRecord] tracking voter addresses → their complete vote records
- `voting_ended`: Boolean flag indicating if voting has concluded
- `end_block`: Block number when voting automatically ends

### VoteRecord Dataclass
Each vote record stores:
- `voter_addr`: Address of the voter
- `option_id`: Selected option ID
- `reasoning`: User-provided reasoning for their vote
- `legitimacy_score`: 0-100 scale from LLM consensus
- `is_legitimate`: Boolean (true if score ≥ 70)
- `timestamp`: When the vote was cast

### Functions

#### Write Functions
- `vote_with_reasoning(option_id: str, reasoning: str)`: Cast a vote with reasoning (uses msg.sender)
- `end_voting()`: Owner function to manually end voting

#### View Functions
- `get_results()`: Get voting results (only after voting ends)
- `get_question()`: Get the voting question
- `has_voted(voter_addr: Address)`: Check if an address has voted
- `get_vote_record(voter_addr: Address)`: Get complete vote record including legitimacy assessment
- `get_voting_status()`: Get current voting status and block information

## Usage

1. Deploy the contract with a question, options, and optional duration:
   ```python
   contract = VotingContract("Best programming language?", ["Python", "JavaScript", "Go"], 10080)
   ```

2. Users vote by calling `vote_with_reasoning()` with option ID and reasoning:
   ```python
   # Users must provide reasoning for their vote
   contract.vote_with_reasoning("opt_0", "I prefer Python for its simplicity and readability")
   ```

3. Owner ends voting with `end_voting()` or it ends automatically at `end_block`

4. Results can be viewed with `get_results()` after voting ends (only counts legitimate votes)

5. Individual vote records can be inspected with `get_vote_record()` to see reasoning and legitimacy scores

## Security & Design Notes

- **Authentication**: Uses `gl.message.sender_address` for voter identity - prevents voting on behalf of others
- **Consensus Threshold**: Legitimacy threshold set at 70/100 (configurable in the `_check_vote_legitimacy` method)
- **Score Tolerance**: ±15 point tolerance allows for reasonable variation in subjective LLM scoring
- **Decision Exactness**: Legitimacy boolean must match exactly between leader and validator
- **Notes Similarity**: 30% word overlap ensures validators are assessing similar aspects

## Testing

Direct mode tests are available in `tests/direct/test_voting.py`

Run tests with:
```bash
python3 -m pytest tests/direct/test_voting.py -v
```

## GenLayer Best Practices Followed

[←] Pinned runner version: `# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }`
[←] Proper storage types: `Address`, `TreeMap[K,V]`, `u256`
[←] Correct contract structure with class-level annotations
[←] Meaningful consensus usage for subjective judgment (vote legitimacy)
←] Proper error handling with classifications
←] Storage rules followed (no fields in `__init__`)
←] Clear separation of view/write functions