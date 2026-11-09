import { readFileSync } from 'node:fs'
import { defineConfig } from 'vitepress'

// The pages, the sidebar and the navigation come from docs/scripts/build_pages.py, which
// writes them to docs/.generated from the repository's own files. Run it before building:
//   python docs/scripts/build_pages.py && npm run build --prefix docs
const site = JSON.parse(readFileSync(new URL('../.generated/site.json', import.meta.url), 'utf8'))

// Heading anchors are made the way GitHub makes them, as in build_pages.py's slugify,
// so a link such as SPEC.md#82-codes works on GitHub and on the site.
const slugify = (text: string) =>
  text.toLowerCase().replace(/[^\p{L}\p{M}\p{N}\p{Pc}\s-]/gu, '').replace(/\s/g, '-')

export default defineConfig({
  title: site.title,
  description: site.description,
  srcDir: '.generated',
  base: process.env.DOCS_BASE ?? '/workflow-spec/',
  cleanUrls: true,
  markdown: { anchor: { slugify } },
  themeConfig: {
    nav: site.nav,
    sidebar: site.sidebar,
    outline: { level: [2, 3] },
    search: { provider: 'local' },
    socialLinks: [{ icon: 'github', link: site.repository }],
    // build_pages.py writes each copied page's edit link into the page, since this function
    // runs in the browser, where nothing else from this file is defined.
    editLink: { pattern: ({ frontmatter }) => frontmatter.editUrl },
  },
})
