// "Paste samples" in demo mode (04_DEMO_SCRIPT Beat 2): the T1 and T2 lines from demo/corpus.
// B7's GET /api/demo/scenario can replace these once it exists.
export const DEMO_SAMPLES = [
  '<134>Sep 26 14:05:09 fw01 app[233]: {"evt":"auth","msg":"user=r.patil OK login from 10.4.1.20 via 10.2.3.4"} | trace=',
  '<134>Sep 26 14:05:09 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma OK login from 10.4.1.21 via 10.2.3.4"} | trace=',
  '<134>Sep 26 14:05:09 fw01 app[233]: {"evt":"auth","msg":"user=neel.k OK login from 10.4.2.9 via 10.2.3.4"} | trace=',
  '<134>Sep 26 14:05:10 fw01 app[233]: {"evt":"session","msg":"session 7781 closed for r.patil after 312s"}',
  '<134>Sep 26 14:05:10 fw01 app[233]: {"evt":"session","msg":"session 7782 closed for a.sharma after 319s"}',
].join("\n");
