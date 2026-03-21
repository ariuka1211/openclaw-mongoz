// src/topics.js

const TOPIC_PATTERNS = {
  technical_decisions: {
    headers: /key rules|lessons|setup|config|architecture|tech|debug|fix|solution|implementation/i,
    content: /\b(use|using|chose|decided|going with|deployed|configured|installed|built|implemented|fixed|debugged|solved|architecture|framework|library|database|server|deploy|config|build|compile|runtime|docker|node|npm|bun|git|ssh|api|endpoint|middleware|plugin|gateway|websocket|auth|token|session|cache|redis|sqlite|postgres|nginx|apache|linux|ubuntu|debian|systemd|service|daemon|cron|port|firewall|ssl|tls|https|dns|domain|cert|certificate|backup|restore|migrate|migration|schema|model|schema|index|optimize|benchmark|performance|memory|cpu|disk|io|latency|throughput|concurrency|thread|async|await|promise|callback|stream|buffer|queue|worker|cluster|scale|load|balance|proxy|reverse|forward|tunnel|vpn|ssh|sftp|rsync|tar|gzip|compress|encrypt|decrypt|hash|salt|bcrypt|jwt|oauth|cors|csrf|xss|sql injection|sanitize|validate|escape|parse|serialize|format|lint|test|unit|integration|e2e|ci|cd|pipeline|deploy|release|rollback|canary|blue.green|feature flag|toggle|monitor|log|trace|debug|profile|inspect|diagnose|incident|outage|hotfix|patch|rollback)\b/i
  },
  project_context: {
    headers: /project|repo|repository|server|team|members|setup|environment|infra|infrastructure|stack/i,
    content: /\b(server|srv|host|ip|domain|subdomain|repo|repository|github|gitlab|bitbucket|branch|commit|merge|pr|pull request|issue|ticket|milestone|sprint|agile|scrum|kanban|jira|confluence|notion|slack|discord|telegram|teams|workspace|organization|org|team|member|contributor|maintainer|owner|admin|role|permission|access|auth|invite|onboard)\b/i
  },
  preferences: {
    headers: /preference|style|opinion|like|hate|prefer|approach|philosophy|vibe/i,
    content: /\b(prefer|like|hate|love|dislike|want|wish|need|expect|expect|avoid|skip|keep|maintain|style|approach|way|method|philosophy|principle|rule|guideline|convention|standard|practice|habit|routine|workflow|pattern|taste|opinion|feel|think|believe)\b/i
  },
  action_items: {
    headers: /todo|task|action|next|follow.?up|plan|roadmap|sprint|backlog/i,
    content: /\b(todo|task|action|need to|should|must|will|going to|plan|schedule|deadline|due|follow.up|check|verify|test|review|audit|investigate|research|explore|try|experiment|prototype|poc|proof.of.concept|spike|chore|errand|fix|patch|update|upgrade|bump|migrate|refactor|optimize|clean|organize|structure|document|write|create|add|remove|delete|fix|resolve|close|complete|finish|done)\b/i
  },
  personal_info: {
    headers: /about|identity|profile|bio|contact|info|timezone|location/i,
    content: /\b(name|timezone|tz|utc|gmt|pst|mst|cst|est|edt|cdt|mdt|pdt|born|live|located|based|from|age|email|phone|address|city|country|state|pronouns|he\/him|she\/her|they\/them)\b/i
  }
};

/**
 * Classify a chunk's content into a topic category.
 * @param {string} content - The chunk content
 * @param {string|null} header - The nearest header
 * @returns {string} - Topic category
 */
export function classifyTopic(content, header = null) {
  let bestTopic = 'general';
  let bestScore = 0;

  for (const [topic, patterns] of Object.entries(TOPIC_PATTERNS)) {
    let score = 0;

    // Check header match (weighted higher)
    if (header && patterns.headers.test(header)) {
      score += 3;
    }

    // Check content patterns
    if (patterns.content) {
      const matches = content.match(new RegExp(patterns.content, 'gi'));
      if (matches) {
        score += matches.length;
      }
    }

    if (score > bestScore) {
      bestScore = score;
      bestTopic = topic;
    }
  }

  return bestTopic;
}
