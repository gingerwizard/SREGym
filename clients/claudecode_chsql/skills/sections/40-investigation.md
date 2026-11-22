## Investigation discipline

Follow these rules before you write a diagnosis. They exist because the common way to be wrong with this data
is not a missing fact but a conclusion drawn too early.

1. **Read the whole object you blame.** Before naming any object as the cause, query its latest full
   manifest (`argMax(Body, Timestamp)` on the `k8s-objects` records) (spec and status) and its controller's (the Deployment or StatefulSet for a Pod, the
   CronJob for a Job) and read every field, not the one you expected: environment variables, ports,
   selectors, probes, resources, node selectors, tolerations, volumes. Name the exact field that is wrong.
2. **Look for a second cause.** When you have one cause, list what it explains and check what it does not:
   go back over the Warning events, the recent changes and the error patterns of every affected service.
   Two independent causes at once is normal here. Report each one.
3. **Origin, not victim.** If an object's state differs from what its controller asked for, something
   changed it: an admission webhook, a controller, an operator, a job. Find that actor and name it. If a
   request is denied for lack of permission, name the Role or ClusterRole whose rules changed, not only
   the subject that was denied.
4. **Credentials: which side changed.** When authentication fails after a rotation, say which side holds
   the new secret and which the old one, and point to the evidence (the change record and the time the
   failures began). Both directions produce the same "authentication failed" logs; do not guess.
5. **Order by time.** The earliest change that precedes the first symptom is your origin candidate; a
   change after the symptoms began is a consequence or a repair attempt.
6. **Say what you verified and what you inferred.** Quote the record (object, field, time) behind each
   claim in the diagnosis.
