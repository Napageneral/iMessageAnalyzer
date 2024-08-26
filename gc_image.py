import io
from PIL import Image, ImageDraw, ImageFont
import matplotlib.pyplot as plt
import numpy as np

class GroupChatImageGenerator:
    def __init__(self):
        self.colors = {
            'background': '#F3F4F6',
            'text': '#1F2937',
            'bar': '#60A5FA'
        }
        self.tapback_types = ['Thumbs Up', 'Thumbs Down', 'Laugh', 'Heart', 'Exclamation', 'Question']

    def generate_group_chat_image(self, chat_name, participant_details):
        # Sort participants by message count
        sorted_participants = sorted(participant_details, key=lambda x: x['message_count'], reverse=True)

        # Create the bar chart
        fig, ax = plt.subplots(figsize=(10, 5))
        names = [p['name'] for p in sorted_participants]
        message_counts = [p['message_count'] for p in sorted_participants]
        
        bars = ax.bar(names, message_counts, color=self.colors['bar'])
        ax.set_ylabel('Number of Messages')
        ax.set_title('Messages Sent by Participant')
        plt.xticks(rotation=45, ha='right')
        
        # Add value labels on top of each bar
        for bar in bars:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height,
                    f'{height}', ha='center', va='bottom')

        # Save the plot to a bytes object
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=300)
        buf.seek(0)
        
        # Create the final image
        chart_image = Image.open(buf)
        width, height = chart_image.size
        final_image = Image.new('RGB', (width, height + 300), color=self.colors['background'])
        final_image.paste(chart_image, (0, 0))
        
        # Add text information
        draw = ImageDraw.Draw(final_image)
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 16)
        
        y_offset = height + 20
        draw.text((20, y_offset), "Top Tapback Givers:", font=font, fill=self.colors['text'])
        y_offset += 30
        
        for tapback in self.tapback_types:
            top_giver = max(participant_details, key=lambda x: x['tapbacks_sent'].get(tapback, 0))
            draw.text((20, y_offset), f"{tapback}: {top_giver['name']}", font=font, fill=self.colors['text'])
            y_offset += 20
        
        y_offset += 20
        draw.text((20, y_offset), "Top Tapback Receivers:", font=font, fill=self.colors['text'])
        y_offset += 30
        
        for tapback in self.tapback_types:
            top_receiver = max(participant_details, key=lambda x: x['tapbacks_received'].get(tapback, 0))
            draw.text((20, y_offset), f"{tapback}: {top_receiver['name']}", font=font, fill=self.colors['text'])
            y_offset += 20

        # Save the final image to bytes
        img_byte_arr = io.BytesIO()
        final_image.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()
        
        return img_byte_arr