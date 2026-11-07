// Workspace dictionary extension (stages 2–3). Deep-merged into ws.* from en.js (src/i18n/merge.js).
// Original dialog text is never translated — interface only.
export default {
  common: { close: 'Close', cancel: 'Cancel', confirm: 'Confirm', save: 'Save', back: 'Back', delete: 'Delete', add: 'Add', yes: 'Yes', no: 'No' },
  evl: { search: 'Search by evaluation ID, conversation and operator', emptyTitle: 'No evaluations yet', emptyText: 'Run an evaluation from the conversation page — the result will appear here.', range: 'Score', version: 'Checklist', onlyGross: 'Only with violation', colId: 'Evaluation ID', colDialog: 'Conversation', back: 'Back to evaluations', notFoundTitle: 'Evaluation not found', notFoundText: 'There is no evaluation “{id}” in the system.', counts: 'Criteria evaluated: {n}, N/A: {na}', result: 'Evaluation result', openDialog: 'Open conversation' },
  mock: { short: 'demo data' },
  list: { reset: 'Reset filters', noMatchTitle: 'Nothing found', noMatchText: 'Change the query or reset the filters.' },
  f: { all: 'All', language: 'Language', channel: 'Channel', status: 'Status', operator: 'Operator' },
  col: { id: 'ID', language: 'Language', channel: 'Channel', operator: 'Operator', status: 'Status', score: 'Score', gross: 'Gross violation', date: 'Date' },
  pager: { label: 'Pagination', prev: 'Previous page', next: 'Next page', range: '{from}–{to} of {total}', page: 'Page {page} of {pages}' },
  state: { aiDown: 'The AI evaluation service is temporarily unavailable. Try again later.', conflict: 'The action conflicts with the current state of the data.', invalid: 'Please check the entered data.', notFound: 'Object not found.', errorTimeout: 'Backend did not respond in time ({url}).' },
  status: {
    conv: { evaluating: 'Evaluating', failed: 'Evaluation failed' },
    appeal: { approved: 'Approved' },
    eval: { running: 'Running', done: 'Done', failed: 'Failed' },
  },
  role: { client: 'Customer', operator: 'Operator', bot: 'Bot', system: 'System' },
  gross: { short: 'Gross', title: 'A gross violation was recorded', found: 'Gross violation detected', none: 'No gross violations' },
  ans: { yes: 'Yes', no: 'No' },
  atype: { yes_no: 'Yes/No', scale: 'Scale', flag: 'Violation flag' },
  crit: { weight: 'Criterion weight', weightN: 'Weight {n}', answer: 'Answer', points: 'Points', na: 'N/A', naReason: 'N/A reason', noEvidence: 'No supporting quotes', violation: 'Violation', noViolation: 'No violation', revised: 'Revised after appeal: {score}' },
  appeal: { open: 'File an appeal', exists: 'Appeal filed', alreadyTip: 'An appeal for this criterion is already in progress.' },
  conv: {
    back: 'Back to conversations', tabTranscript: 'Transcript', tabEval: 'AI evaluation', 
    search: 'Search by conversation ID and message text', emptyTitle: 'No conversations yet', emptyText: 'Import conversations or wait for them to arrive from Backend.',
    import: 'Import conversations', importTitle: 'Import conversations', importText: 'Upload a JSON file with a list of conversations. The expected format is below (contract not yet agreed with Backend):',
    importFile: 'JSON file', importReady: 'Conversations in file: {n}', importRun: 'Import', importDone: 'Imported: {n}, skipped: {s}', importFail: 'Import failed', importBadFile: 'The file does not look like a conversation list: a JSON array is required.',
    transcript: 'Transcript', msgCount: 'Messages: {n}', noMessages: 'This conversation has no messages', recording: 'Call recording', noAudio: 'No audio: Backend did not provide audio_url.',
    notFoundTitle: 'Conversation not found', notFoundText: 'There is no conversation “{id}” in the system.', assign: 'Assign operator', reassign: 'Change operator',
  },
  assign: { title: 'Operator assignment', text: 'Backend does not provide an operator for this conversation — specify one manually.', known: 'From known operators', custom: 'Or enter an operator ID', required: 'Specify an operator', done: 'Operator {id} assigned' },
  ev: { notFound: 'The conversation has no message “{id}”. The quote from the evaluation cannot be shown.' },
  eval: {
    title: 'AI evaluation', version: 'Version', versionN: 'checklist v{n}', current: 'current', run: 'Run evaluation', runAnother: 'Evaluate with another version', runWith: 'Checklist version', running: 'Evaluating…',
    noFree: 'This conversation is already evaluated with every checklist version.', noneTitle: 'Not evaluated yet', noneText: 'Run the evaluation — the result will appear here.', runningTitle: 'Evaluation in progress', runningText: 'This usually takes a few seconds.',
    failTitle: 'Evaluation failed', done: 'Evaluation ready', total: 'Total score', afterAppeals: 'After appeals', fromBackend: 'Score is calculated by Backend',
  },
};
