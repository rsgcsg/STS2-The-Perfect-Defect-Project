# Public compact v2 D-Simple-S pilot

Engineering evidence only. Producer source: `6d3c46d29c45e591cbdb332a38ba9f0443c1544b`.
This is a new input/model identity, not a revision of the earlier execution-semantic pilot.

| Artifact | Identity |
|---|---|
| Allocation | `e50c3510897d8c4657f71e47a1f82bb617b0dc839c9bfa4012161aeaeed4afc5` |
| Public view | `2553accb6bf393809f0f813f94f73a1ccd266cdb6824a38e1591b1a1ebd478fd` |
| S input | `f5f1101aeb6c6ec285d39b598f89ca3b97cf4684d66143d5238bb3cea737d18e` |
| PF input prepared for comparison | `e5eb679cd2fb4873010af2be1995539cafacff47572d8d9b64b5aa62028b303e` |
| Run | `0128fadf16b3209b32903ed910e92189b7018efb2ccd23be990cf598a43606ad` |
| Checkpoint | `26d96df677985de647f7c3a5df3b795c2f51f9487b7fa92a60de65f6aac64a2a` |
| Result | `9d8025b26bd044b5965c3c1db090721744930823b4643ce9706ba0ec1c4f2224` |
| Model | `38c20c79bb5e538886005f94dd48cf339a740d9660b70ee16257abde6ba2c0bb` |
| Evaluation | `7b2aa2d95068206fd2188a2b11a09306afbd791e646ae298bfe392db44d909e2` |

The unchanged allocation admits 49 train and 16 dev decisions under exact Human-public
binding; one native-only training entry remains explicitly excluded, not deleted.
No test/Gold labels are used. Both tokenizers preserve all admitted facts/candidates.
Maximum joint lengths are S 5284 and PF 5582, with a configurable 16384 resource budget.

`stage1a.dsimple.s.v1`, seed 1701, MPS FP32 completed 10 single-decision updates.
Input preparation took 43.310 seconds; the training/evaluation worker reported 30.349
seconds; the complete wrapper took 74.984 seconds. These include verification/storage
overheads and are not pure training throughput. Stderr was empty.

Dev Top-1 is 8/16, or 6/14 on multi-candidate decisions; NLL 1.7202640384 and MRR
0.6427827381. Uniform-legal and action-only tie-aware Top-1 are 0.2778792388 and
0.34375. One independent dev run and 10 updates cannot establish useful strategy,
generalization or superiority. Comparing against the old semantic-input pilot's
75% would confound the changed input contract.

## Export and original-public-snapshot parity

All 16 dev entries were reopened from their verified original source archives. Their
public texts and bindings reproduce the published view. Checkpoint, exported scorer
and `score_snapshot` produce identical scores; reversing candidates reverses scores
exactly in this check. A separate process in `/private/tmp`, given only the export and
an unlabelled public snapshot, returns all 18 candidate scores identically by BoundAction
ID, without a training-store argument. JSON object key order is not candidate order.

Cold scorer load took 0.063789 seconds; resident scoring ranged 0.007184–0.196373 seconds
(mean 0.103931) across this serial check. Warm-up is uncontrolled; this is not a game
end-to-end latency benchmark. Native game execution and Workbench registration remain
pending. Historical recovery evidence remains attached to its original model/source.

Private job directory: `stage1a-public-dsimple-s-20260918-183353` under the research root.
Export: `stage1a-exports/dsimple-s-public-v2`. Raw snapshots, logs, weights and the full
verification receipt remain outside Git. Receipt `export-check.json` SHA-256:
`884e3cf31606ec5f0c25aff18dec1bdd512972667855130492fe529823e9f0c3`.
