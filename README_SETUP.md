# Where each file goes

Copy these into the ROOT of your hackathon project (the folder with .git):

your-project/
├── AGENTS.md                                   <- rules (always loaded)
├── docs/
│   └── SPEC.md                                 <- the problem statement
└── .agents/
    └── skills/
        └── hackathon-builder/
            └── SKILL.md                        <- workflow skill

Then start the CLI from inside your-project/ and run /skills to confirm.
(If the skill is not listed, rename .agents to .agent and restart.)
