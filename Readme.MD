# The Problem
Teams use Git as "source of truth" for what should run in Kubernetes. But in reality, someone does kubectl edit during an incident, a webhook auto-modifies a resource, an operator silently changes something. Now your cluster doesn't match Git — that's drift. Nobody notices until something breaks.
# The Solution
A CLI tool that answers one question: "Does my cluster match my Git?" It compares what's declared in Git vs what's actually running, flags the differences by severity, and shows them in terminal/HTML/web views.