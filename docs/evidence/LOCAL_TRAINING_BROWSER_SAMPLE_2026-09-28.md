# Local small-B browser sample, 2026-09-28

Scope: application engineering evidence, not Human gameplay, production activation,
real-recording training, model quality or game-inference qualification.

The exercised clean source was `4bbc4af8caba9bac45beaf06ed926de8f94820aa`.
An isolated temporary Workbench used the same `Application`, server and training
owner as the product, with a private three-run synthetic verified-recording fixture.
The configured production profile, store, models and game were not changed.
The harness carried the actual application instance/configuration identity and used
the normal browser cookie, Origin and CSRF boundary. Its only source fixture setup
patches were removed before the server and worker started.

Observed in Safari through the ordinary interface:

1. Dataset detail displayed six canonical decisions, training purpose and an available
   training/development allocation. Rendering did not launch a worker.
2. One click on **开始本机短训练** showed a pending operation and removed the start
   button. The fixed preset ran three CPU steps with two threads.
3. **刷新训练状态** displayed completion and links to the exact result, model and
   development report.
4. **查看开发集结果** opened that report's local artifact detail, with two development
   decisions, its model/view identities and model/uniform/action-only metrics.
5. The report's model link opened the corresponding local model artifact detail.

The dev fixture had **zero multi-candidate decisions**: its perfect top-1 is trivial
and does not demonstrate useful learning. Multi-candidate scoring/binding is covered
by separate synthetic model tests, not inferred from this browser journey.

Exact synthetic run: `c82bdf86b665262ac85278bb8ade8c8d8e2ac8db50047bd5515b2ec92bd3f601`.
Result: `fd4a1803c56a365b6b70d30602765076eaab73387ad3f794b16836c70430ae07`.
Evaluation: `25564aad285a301f9913367636be88e3bd38ab2686d0c2a3ff8eff83e773467d`.

The temporary server was explicitly closed after completion and returned exit 0;
the browser returned to the existing production library. No game process was started,
no model was registered for inference, and no team service was contacted.

Related local verification: the combined Node/vm file at this source reported
106 passed, zero failed/skipped. The authenticated HTTP regression at its reviewed
parent `c4a9d9fd9fdfce8e0fc0bee9811cb9b1ad55855f` reported 9 passed and checks exact
completion identities, repeated GET without new artifacts, and response privacy.
These are bounded checks; the final candidate still needs its selected hosted gate.
