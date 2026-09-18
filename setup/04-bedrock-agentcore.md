# AWS access — Day 4 only

**You do not need any of this for Days 1, 2 or 3.** Until Day 4 everything goes
through the gateway with your `sk-` key. If you are reading this on Day 1, you
are in the wrong file — go to [03-claude-code.md](03-claude-code.md).

---

## Why there are two ways in

| | Days 1–3 | Day 4 (AgentCore) |
|---|---|---|
| You use | Claude Code, VS Code, lab code | AgentCore toolkit, Bedrock SDK |
| Credential | your `sk-` gateway key | your AWS access key |
| Model named as | `claude-sonnet`, `claude-opus`, `claude-haiku` | a long ARN (below) |
| Who tracks cost | the gateway | AWS, per person |

AgentCore needs real AWS credentials — it cannot go through the gateway — which
is why you get a second set of credentials for one day only.

## What the ARNs on your card are

Your card lists three lines like this:

```
sonnet : arn:aws:bedrock:ap-south-1:...:application-inference-profile/m2ea6i9bkq50
haiku  : arn:aws:bedrock:ap-south-1:...:application-inference-profile/mc3jk1csr9o4
opus   : arn:aws:bedrock:ap-south-1:...:application-inference-profile/mlknst57r5w6
```

An **application inference profile** is an AWS object that points at a model and
carries a tag saying who owns it. Yours are named `aira_pNN-sonnet5`,
`aira_pNN-haiku45` and `aira_pNN-opus48`.

They exist for one reason: **so AWS can bill your usage to you by name.** Calling
the shared model id directly (`global.anthropic.claude-sonnet-5`) is denied on
purpose — it would work, but nobody could tell whose spend it was.

So on Day 4, wherever a tutorial says `model_id="anthropic.claude-..."`, you put
**your own profile ARN** instead.

## Configure the AWS CLI

`~/.aws/credentials`

```ini
[airamatrix]
aws_access_key_id = <from your card>
aws_secret_access_key = <from your card>
```

`~/.aws/config`

```ini
[profile airamatrix]
region = ap-south-1
```

Check it:

```bash
export AWS_PROFILE=airamatrix
aws sts get-caller-identity          # should show .../aira_pNN
```

Then prove you can invoke, using **your own sonnet ARN**:

```bash
aws bedrock-runtime invoke-model --region ap-south-1 \
  --model-id "<your sonnet ARN>" \
  --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":32,"messages":[{"role":"user","content":"say OK"}]}' \
  --cli-binary-format raw-in-base64-out /tmp/out.json && cat /tmp/out.json
```

## Rules on Day 4

- **Name everything with your participant id**: `aira_pNN_ticket_agent`. ECR,
  CodeBuild and S3 permissions key off that prefix.
- **Tag AgentCore resources** `participant=aira_pNN`. Untagged creates are denied.
- **Use the execution role on your card.** You cannot create IAM roles.
- **Underscores, not hyphens**, in AgentCore agent names.

## What is blocked, and why

| Blocked | Reason |
|---|---|
| `global.anthropic.*` called directly | Bypasses per-person cost attribution |
| Opus 5, 4.7, 4.6, 4.5, Fable, OpenAI, Stability | Not approved for the programme |
| Creating inference profiles | Would route around the attribution above |
| Any region except `ap-south-1` (except model calls) | Keeps the blast radius small |
| S3 outside `bedrock-agentcore-*` | Protects the host account |

A daily cap applies to this direct path. If you hit it, model calls start
failing with an explicit deny — tell the trainer, it is cleared on request.
