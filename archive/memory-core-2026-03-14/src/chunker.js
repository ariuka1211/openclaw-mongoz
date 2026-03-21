// src/chunker.js
import { createHash } from 'crypto';
import { join } from 'path';

/**
 * Extract YAML frontmatter from markdown content
 * @param {string} content - Markdown content
 * @returns {{frontmatter: string, rest: string}} - Object with frontmatter and remaining content
 */
function extractFrontmatter(content) {
  const frontmatterRegex = /^---\s*\n([\s\S]*?)\n---\s*\n/;
  const match = content.match(frontmatterRegex);
  
  if (match) {
    return {
      frontmatter: match[0], // Include the delimiters
      rest: content.substring(match.index + match[0].length)
    };
  }
  
  return {
    frontmatter: null,
    rest: content
  };
}

/**
 * Split content by double newlines (paragraphs), respecting code blocks
 * @param {string} content - Content to split
 * @returns {string[]} - Array of paragraphs
 */
function splitByParagraphsRespectingCodeBlocks(content) {
  const paragraphs = [];
  let current = '';
  let inCodeBlock = false;
  let lines = content.split('\n');
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    
    // Toggle code block state
    if (line.trim().startsWith('```')) {
      inCodeBlock = !inCodeBlock;
    }
    
    current += line + '\n';
    
    // If we're not in a code block and we hit a blank line, that's a paragraph break
    if (!inCodeBlock && line.trim() === '' && i < lines.length - 1) {
      // Check if next line is also blank (to avoid multiple breaks)
      if (lines[i + 1].trim() !== '') {
        paragraphs.push(current.trim());
        current = '';
      }
    }
  }
  
  // Add remaining content
  if (current.trim()) {
    paragraphs.push(current.trim());
  }
  
  return paragraphs;
}

/**
 * Chunk markdown content according to the rules
 * @param {string} filePath - Path to the file
 * @param {string} content - Markdown content
 * @returns {Array} - Array of chunk objects
 */
export function chunkMarkdown(filePath, content) {
  const chunks = [];
  let chunkIndex = 0;
  
  // Extract frontmatter first
  const { frontmatter, rest } = extractFrontmatter(content);
  
  // If there's frontmatter, make it its own chunk
  if (frontmatter) {
    const frontmatterHash = createHash('sha256')
      .update(filePath + '_frontmatter_' + chunkIndex)
      .digest('hex');
    
    chunks.push({
      id: frontmatterHash,
      file_path: filePath,
      chunk_index: chunkIndex,
      content: frontmatter,
      header: null, // Frontmatter has no header
      source_hash: createHash('sha256').update(frontmatter).digest('hex')
    });
    
    chunkIndex++;
  }
  
  // Process the rest of the content
  let remainingContent = rest;
  
  // Split by headers (## and ###)
  const headerRegex = /^(##+)\s+(.+)$/gm;
  let lastMatchEnd = 0;
  let match;
  
  // We'll process sections between headers
  const sections = [];
  
  // Find all headers
  const headerPositions = [];
  while ((match = headerRegex.exec(remainingContent)) !== null) {
    headerPositions.push({
      index: match.index,
      level: match[1].length,
      text: match[2],
      fullMatch: match[0]
    });
  }
  
  // If no headers found, treat entire content as one section
  if (headerPositions.length === 0) {
    sections.push({
      header: null,
      content: remainingContent,
      start: 0,
      end: remainingContent.length
    });
  } else {
    // Add content before first header
    if (headerPositions[0].index > 0) {
      sections.push({
        header: null,
        content: remainingContent.substring(0, headerPositions[0].index),
        start: 0,
        end: headerPositions[0].index
      });
    }
    
    // Process each header section
    for (let i = 0; i < headerPositions.length; i++) {
      const header = headerPositions[i];
      const start = header.index;
      let end = remainingContent.length;
      
      // If there's a next header, end at its start
      if (i + 1 < headerPositions.length) {
        end = headerPositions[i + 1].index;
      }
      
      const headerText = `${header.level === 2 ? '##' : '###'} ${header.text}`;
      const sectionContent = remainingContent.substring(start, end);
      
      sections.push({
        header: headerText,
        content: sectionContent,
        start: start,
        end: end
      });
    }
  }
  
  // Process each section
  for (const section of sections) {
    let sectionContent = section.content;
    
    // If section is small enough (< 800 chars), keep as one chunk
    if (sectionContent.length <= 800) {
      if (sectionContent.trim()) {
        const hashInput = filePath + '_' + chunkIndex;
        const chunkId = createHash('sha256').update(hashInput).digest('hex');
        
        chunks.push({
          id: chunkId,
          file_path: filePath,
          chunk_index: chunkIndex,
          content: sectionContent,
          header: section.header,
          source_hash: createHash('sha256').update(sectionContent).digest('hex')
        });
        
        chunkIndex++;
      }
    } else {
      // Section is large, split by paragraphs
      const paragraphs = splitByParagraphsRespectingCodeBlocks(sectionContent);
      
      for (const paragraph of paragraphs) {
        if (paragraph.trim()) {
          const hashInput = filePath + '_' + chunkIndex;
          const chunkId = createHash('sha256').update(hashInput).digest('hex');
          
          chunks.push({
            id: chunkId,
            file_path: filePath,
            chunk_index: chunkIndex,
            content: paragraph,
            header: section.header,
            source_hash: createHash('sha256').update(paragraph).digest('hex')
          });
          
          chunkIndex++;
        }
      }
    }
  }
  
  return chunks;
}