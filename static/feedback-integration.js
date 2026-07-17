// static/feedback-integration.js - Feedback integration for chat UI
// Adds thumbs up/down feedback buttons to assistant messages

class FeedbackManager {
    constructor() {
        this.feedbackGiven = new Set();
        this.processedElements = new WeakSet();
        this.init();
    }

    init() {
        this.addFeedbackStyles();
        this.startObserving();
        console.log('[Feedback] System initialized');
    }

    addFeedbackStyles() {
        const style = document.createElement('style');
        style.textContent = `
            .feedback-container {
                display: flex;
                align-items: center;
                gap: 10px;
                padding: 8px 0;
                border-top: 1px solid rgba(0,0,0,0.08);
                margin-top: 12px;
                padding-top: 12px;
            }

            .feedback-label {
                font-size: 13px;
                color: #888;
                margin-right: 4px;
            }

            .feedback-btn {
                background: none;
                border: 1px solid transparent;
                cursor: pointer;
                font-size: 17px;
                padding: 4px 8px;
                border-radius: 6px;
                transition: all 0.2s ease;
                line-height: 1;
            }

            .feedback-btn:hover:not(:disabled) {
                transform: scale(1.15);
                background-color: rgba(0,0,0,0.04);
            }

            .feedback-btn:disabled {
                cursor: default;
                opacity: 0.5;
            }

            .feedback-btn.thumbs-up.active {
                color: #4CAF50;
                background-color: #e8f5e9;
                border-color: #c8e6c9;
            }

            .feedback-btn.thumbs-down.active {
                color: #f44336;
                background-color: #ffebee;
                border-color: #ffcdd2;
            }

            .feedback-success {
                font-size: 12px;
                color: #4CAF50;
                margin-left: 8px;
                animation: fbFadeIn 0.3s ease;
            }

            @keyframes fbFadeIn {
                from { opacity: 0; transform: translateY(-4px); }
                to   { opacity: 1; transform: translateY(0); }
            }

            .feedback-modal-overlay {
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                background-color: rgba(0, 0, 0, 0.5);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 10000;
                animation: fbFadeIn 0.2s ease;
            }

            .feedback-modal {
                background: white;
                padding: 24px;
                border-radius: 12px;
                max-width: 480px;
                width: 90%;
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
            }

            .feedback-modal h3 {
                margin: 0 0 12px 0;
                color: #333;
                font-size: 17px;
            }

            .feedback-modal p {
                margin: 0 0 14px 0;
                color: #666;
                font-size: 14px;
                line-height: 1.4;
            }

            .feedback-textarea {
                width: 100%;
                min-height: 80px;
                padding: 12px;
                border: 2px solid #e1e5e9;
                border-radius: 8px;
                font-size: 14px;
                font-family: inherit;
                resize: vertical;
                transition: border-color 0.2s ease;
                box-sizing: border-box;
            }

            .feedback-textarea:focus {
                outline: none;
                border-color: #1754ff;
            }

            .feedback-modal-actions {
                display: flex;
                justify-content: flex-end;
                gap: 10px;
                margin-top: 16px;
            }

            .feedback-modal-btn {
                padding: 9px 18px;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.2s ease;
            }

            .feedback-modal-btn.cancel {
                background: #f1f3f5;
                color: #6c757d;
            }

            .feedback-modal-btn.cancel:hover {
                background: #e9ecef;
            }

            .feedback-modal-btn.submit {
                background: #1754ff;
                color: white;
            }

            .feedback-modal-btn.submit:hover:not(:disabled) {
                background: #0d3fd4;
            }

            .feedback-modal-btn:disabled {
                opacity: 0.6;
                cursor: not-allowed;
            }
        `;
        document.head.appendChild(style);
    }

    startObserving() {
        // Strategy 1: MutationObserver on the #messages container
        //   - watches for new <li> nodes being added (real-time sends)
        //   - watches for innerHTML changes (history load)
        const messagesEl = document.getElementById('messages');
        if (messagesEl) {
            const observer = new MutationObserver(() => {
                // Debounce: wait a tick for DOM to settle
                clearTimeout(this._scanTimer);
                this._scanTimer = setTimeout(() => this.scanAllMessages(), 150);
            });
            observer.observe(messagesEl, {
                childList: true,
                subtree: true,
                characterData: true,
            });
            console.log('[Feedback] Observing #messages container');
        }

        // Strategy 2: Periodic scan as fallback
        //   - catches any messages the observer misses
        //   - runs every 2 seconds, very lightweight (skips already-processed)
        this._pollInterval = setInterval(() => this.scanAllMessages(), 2000);

        // Strategy 3: Initial scan after page load
        setTimeout(() => this.scanAllMessages(), 1500);
    }

    scanAllMessages() {
        const aiMessages = document.querySelectorAll('li.msg.ai');
        aiMessages.forEach(msg => {
            // Skip if already processed
            if (this.processedElements.has(msg)) return;
            // Skip if already has feedback buttons
            if (msg.querySelector('.feedback-container')) return;
            // Skip refs-only messages
            if (msg.classList.contains('refs')) return;
            // Skip if still showing typing indicator
            if (msg.querySelector('.typing')) return;
            // Skip if no real content yet (empty or just whitespace)
            const answerContent = msg.querySelector('.answer-content');
            const hasText = answerContent
                ? answerContent.textContent.trim().length > 0
                : msg.textContent.trim().length > 10;
            if (!hasText) return;

            // This message is ready — add feedback buttons
            this.addFeedbackToMessage(msg);
            this.processedElements.add(msg);
        });
    }

    addFeedbackToMessage(messageElement) {
        const messageData = this.extractMessageData(messageElement);
        if (!messageData.answer || messageData.answer.length < 5) return;

        const feedbackContainer = this.createFeedbackContainer(messageData);
        messageElement.appendChild(feedbackContainer);
        console.log('[Feedback] Added buttons to message');
    }

    extractMessageData(messageElement) {
        return {
            conversationId: this.getConversationId(),
            clientId: this.getClientId(),
            question: this.getLastUserQuestion(messageElement),
            answer: this.getMessageText(messageElement)
        };
    }

    getConversationId() {
        const clientId = this.getClientId();
        const storagePrefix = `${clientId}_`;
        return (
            sessionStorage.getItem(`${storagePrefix}CONV_ID`) ||
            window.currentConversationId ||
            localStorage.getItem('conversationId') ||
            'default-conversation'
        );
    }

    getClientId() {
        return (
            window.CLIENT_ID ||
            window.currentClientId ||
            localStorage.getItem('clientId') ||
            'default-client'
        );
    }

    getLastUserQuestion(messageElement) {
        // Walk backwards through siblings to find user message (class "msg u")
        let current = messageElement.previousElementSibling;
        while (current) {
            if (current.classList.contains('u')) {
                return current.textContent.trim();
            }
            // Skip refs messages
            if (current.classList.contains('ai') && !current.classList.contains('refs')) {
                break; // Hit another AI message without finding user msg
            }
            current = current.previousElementSibling;
        }
        return '';
    }

    getMessageText(element) {
        // Try .answer-content first (production's buildAnswer structure)
        const answerContent = element.querySelector('.answer-content');
        if (answerContent) return answerContent.textContent.trim();

        // Fallback: get full text minus feedback and refs
        const clone = element.cloneNode(true);
        const fb = clone.querySelector('.feedback-container');
        if (fb) fb.remove();
        const refs = clone.querySelector('.refs-section');
        if (refs) refs.remove();
        return clone.textContent.trim();
    }

    createFeedbackContainer(messageData) {
        const container = document.createElement('div');
        container.className = 'feedback-container';

        const messageId = this.generateMessageId(messageData);

        const label = document.createElement('span');
        label.className = 'feedback-label';
        label.textContent = 'Was this helpful?';

        const thumbsUp = document.createElement('button');
        thumbsUp.className = 'feedback-btn thumbs-up';
        thumbsUp.dataset.type = 'thumbs_up';
        thumbsUp.dataset.messageId = messageId;
        thumbsUp.textContent = '\uD83D\uDC4D';
        thumbsUp.title = 'Helpful';

        const thumbsDown = document.createElement('button');
        thumbsDown.className = 'feedback-btn thumbs-down';
        thumbsDown.dataset.type = 'thumbs_down';
        thumbsDown.dataset.messageId = messageId;
        thumbsDown.textContent = '\uD83D\uDC4E';
        thumbsDown.title = 'Not helpful';

        container.appendChild(label);
        container.appendChild(thumbsUp);
        container.appendChild(thumbsDown);

        thumbsUp.addEventListener('click', () => {
            this.handleFeedback(thumbsUp, 'thumbs_up', messageData);
        });

        thumbsDown.addEventListener('click', () => {
            this.handleFeedback(thumbsDown, 'thumbs_down', messageData);
        });

        return container;
    }

    generateMessageId(messageData) {
        const raw = (messageData.answer || '').substring(0, 50);
        try {
            return btoa(unescape(encodeURIComponent(raw))).replace(/[^a-zA-Z0-9]/g, '').substring(0, 16);
        } catch (e) {
            return 'msg_' + Date.now().toString(36);
        }
    }

    async handleFeedback(button, feedbackType, messageData) {
        const messageId = button.dataset.messageId;
        if (this.feedbackGiven.has(messageId)) return;

        if (feedbackType === 'thumbs_up') {
            await this.submitFeedback(feedbackType, messageData, null);
            this.markFeedbackGiven(button.closest('.feedback-container'), feedbackType);
        } else {
            this.showCommentModal(messageData, (comment) => {
                this.submitFeedback(feedbackType, messageData, comment);
                this.markFeedbackGiven(button.closest('.feedback-container'), feedbackType);
            });
        }
    }

    async submitFeedback(feedbackType, messageData, comment) {
        try {
            const response = await fetch('/api/feedback/submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    conversation_id: messageData.conversationId,
                    client_id: messageData.clientId,
                    question: messageData.question,
                    answer: messageData.answer,
                    feedback_type: feedbackType,
                    comment: comment,
                    user_id: null
                })
            });

            if (response.ok) {
                console.log('[Feedback] Submitted:', feedbackType);
            } else {
                console.error('[Feedback] Submit failed:', response.status);
            }
        } catch (error) {
            console.error('[Feedback] Error:', error);
        }
    }

    markFeedbackGiven(container, feedbackType) {
        const messageId = container.querySelector('.feedback-btn').dataset.messageId;
        this.feedbackGiven.add(messageId);

        container.querySelectorAll('.feedback-btn').forEach(btn => {
            btn.disabled = true;
            if (btn.dataset.type === feedbackType) {
                btn.classList.add('active');
            }
        });

        const successMsg = document.createElement('span');
        successMsg.className = 'feedback-success';
        successMsg.textContent = 'Thanks for the feedback!';
        container.appendChild(successMsg);
    }

    showCommentModal(messageData, onSubmit) {
        const overlay = document.createElement('div');
        overlay.className = 'feedback-modal-overlay';

        overlay.innerHTML = `
            <div class="feedback-modal">
                <h3>Help us improve</h3>
                <p>What could be better? Your feedback helps us give more accurate answers.</p>
                <textarea class="feedback-textarea"
                    placeholder="Please tell us what went wrong or how we can improve..."
                    maxlength="500"></textarea>
                <div class="feedback-modal-actions">
                    <button class="feedback-modal-btn cancel">Cancel</button>
                    <button class="feedback-modal-btn submit">Submit</button>
                </div>
            </div>
        `;

        const textarea = overlay.querySelector('.feedback-textarea');
        const cancelBtn = overlay.querySelector('.cancel');
        const submitBtn = overlay.querySelector('.submit');

        cancelBtn.addEventListener('click', () => overlay.remove());

        submitBtn.addEventListener('click', () => {
            onSubmit(textarea.value.trim() || null);
            overlay.remove();
        });

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.remove();
        });

        document.addEventListener('keydown', function handler(e) {
            if (e.key === 'Escape') {
                overlay.remove();
                document.removeEventListener('keydown', handler);
            }
        });

        document.body.appendChild(overlay);
        textarea.focus();
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.feedbackManager = new FeedbackManager();
    });
} else {
    window.feedbackManager = new FeedbackManager();
}
