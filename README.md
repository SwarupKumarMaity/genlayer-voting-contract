# Simple Voting Contract

A simple GenLayer intelligent contract for conducting polls and votes.

## Features

- Create a voting question with multiple options
- Users can vote for one option
- Prevents double voting
- Owner can end voting period
- View results after voting ends
- Timeout-based voting period

## Contract Details

### Storage
- `owner`: Address of contract creator
- `question`: The voting question
- `options`: TreeMap of vote options with descriptions and vote counts
- `voters`: TreeMap tracking which addresses have voted and for what
- `voting_ended`: Boolean flag indicating if voting has concluded
- `end_block`: Block number when voting automatically ends

### Functions

#### Write Functions
- `vote(voter_addr: Address, option_id: str)`: Cast a vote for an option
- `end_voting()`: Owner function to manually end voting

#### View Functions
- `get_results()`: Get voting results (only after voting ends)
- `get_question()`: Get the voting question
- `has_voted(voter_addr: Address)`: Check if an address has voted
- `get_voting_status()`: Get current voting status and block information

## Usage

1. Deploy the contract with a question, options, and optional duration:
   ```python
   contract = VotingContract("Best programming language?", ["Python", "JavaScript", "Go"], 10080)
   ```

2. Users vote by calling `vote()` with their address and option ID

3. Owner ends voting with `end_voting()` or it ends automatically at `end_block`

4. Results can be viewed with `get_results()` after voting ends

## Testing

Direct mode tests are available in `tests/direct/test_voting.py`

Run tests with:
```bash
python3 -m pytest tests/direct/test_voting.py -v
```